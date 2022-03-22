
from __future__ import annotations

import abc
import dataclasses
import typing as t
from pathlib import Path

from databind.core.annotations import union
from nr.util.functional import Once

from slam.configuration import Configuration

if t.TYPE_CHECKING:
  from slam.plugins import RepositoryHandlerPlugin
  from slam.project import Project
  from slam.util.vcs import Vcs


@dataclasses.dataclass
class Issue:
  """ Represents an issue. """

  id: str
  url: str
  shortform: str


@dataclasses.dataclass
class PullRequest:
  """ Represents a pull request. """

  id: str
  url: str
  shortform: str


@union(union.Subtypes.entrypoint('slam.plugins.repository_host'))
class RepositoryHost(abc.ABC):
  """ Interface for repository hosting services to resolve issue and pull request references, comment on issues
  and create releases. """

  ENTRYPOINT = 'slam.plugins.repository_host'

  @abc.abstractmethod
  def get_username(self, repository: Repository) -> str | None: ...

  @abc.abstractmethod
  def get_issue_by_reference(self, issue_reference: str) -> Issue: ...

  @abc.abstractmethod
  def get_pull_request_by_reference(self, pr_reference: str) -> PullRequest: ...

  @abc.abstractmethod
  def comment_on_issue(self, issue_reference: str, message: str) -> None: ...

  @abc.abstractmethod
  def create_release(self, version: str, description: str, attachments: list[Path]) -> None: ...

  @staticmethod
  @abc.abstractmethod
  def detect_repository_host(repository: Repository) -> RepositoryHost | None: ...


class Repository(Configuration):
  """ A repository represents a directory that contains one or more projects. A repository represents one or more
  projects in one logical unit, usually tracked by a single version control repository. The class  """

  handler: Once[RepositoryHandlerPlugin]

  def __init__(self, directory: Path) -> None:
    super().__init__(directory)
    self._handler = Once(self._get_repository_handler)
    self.projects = Once(self._get_projects)
    self.vcs = Once(self._get_vcs)
    self.host = Once(self._get_repository_host)

  @property
  def is_monorepo(self) -> bool:
    if len(self.projects()) > 1 or (len(self.projects()) == 1 and self.projects()[0].directory != self.directory):
      return True
    return False

  def load(self) -> None:
    """ Loads the repository handler internally. This method is supposed to be called for catching the potential
    #NotASlamRepository exception if the repository is incompatible.

    Raises:
      NotASlamRepository: If the #directory does not appear to be manageable by the Slam default handler.
        This will not be raised if the repository handler was overwritten in the config and doesn't match.
    """

    self._handler.get()

  def _get_repository_handler(self) -> RepositoryHandlerPlugin:
    """ Returns the handler for this repository. """

    from nr.util.plugins import load_entrypoint
    from slam.plugins import RepositoryHandlerPlugin
    from slam.ext.repository_handlers.default import DefaultRepositoryHandler

    handler_name = self.raw_config().get('repository', {}).get('handler')
    if handler_name is None:
      handler = DefaultRepositoryHandler()
      if not handler.matches_repository(self):
        raise NotASlamRepository(self.directory)
    else:
      assert isinstance(handler_name, str), repr(handler_name)
      handler = load_entrypoint(RepositoryHandlerPlugin, handler_name)()  # type: ignore[misc]
      assert handler.matches_repository(self), (self, handler)
    return handler

  def _get_projects(self) -> list[Project]:
    """ Returns the projects provided by the project handler, but sorted in topological order. """

    from nr.util.digraph import DiGraph
    from nr.util.digraph.algorithm.topological_sort import topological_sort

    projects = sorted(self._handler().get_projects(self), key=lambda p: p.id)
    graph: DiGraph[Project, None, None] = DiGraph()
    for project in projects:
      graph.add_node(project, None)
      for dep in project.get_interdependencies(projects):
        graph.add_node(dep, None)
        graph.add_edge(dep, project, None)

    return list(topological_sort(graph, sorting_key=lambda p: p.id))

  def _get_vcs(self) -> Vcs | None:
    return self._handler().get_vcs(self)

  def _get_repository_host(self) -> RepositoryHost | None:
    return self._handler().get_repository_host(self)


class NotASlamRepository(Exception):
  """ This exception is raised when the default #RepositoryHandlerPlugin is loaded but the #Repository cannot be
  handled by it. This is for example if the directory does not appear to be a directory that should be managed using
  Slam at all."""

  def __init__(self, directory: Path) -> None:
    self.directory = directory
