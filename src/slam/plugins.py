
from __future__ import annotations

import abc
import typing as t

from databind.core.annotations import union
from nr.util.generic import T

if t.TYPE_CHECKING:
  from pathlib import Path
  from poetry.core.semver.version import Version  # type: ignore[import]
  from slam.application import Application, IO
  from slam.check import Check
  from slam.project import Dependencies, Package, Project
  from slam.release import VersionRef
  from slam.repository import Repository
  from slam.util.vcs import Vcs, VcsHost


class ApplicationPlugin(t.Generic[T], abc.ABC):
  """ A plugin that is activated on application load, usually used to register additional CLI commands. """

  ENTRYPOINT = 'slam.plugins.application'

  @abc.abstractmethod
  def load_configuration(self, app: Application) -> T:
    """ Load the configuration of the plugin. Usually, plugins will want to read the configuration from the Slam
    configuration, which is either loaded from `pyproject.toml` or `slam.toml`. Use #Application.raw_config
    to access the Slam configuration. """

  @abc.abstractmethod
  def activate(self, app: Application, config: T) -> None:
    """ Activate the plugin. Register a #Command to #Application.cleo or another type of plugin to
    the #Application.plugins registry. """


class RepositoryHandlerPlugin(abc.ABC):
  """ A plugin to provide data and operations on a repository level. """

  ENTRYPOINT = 'slam.plugins.repository'

  @abc.abstractmethod
  def matches_repository(self, repository: Repository) -> bool:
    """ Return `True` if the handler is able to provide data for the given project. """

  @abc.abstractmethod
  def get_vcs(self, repository: Repository) -> Vcs | None:
    """ Return the version control system that the repository is managed with. """

  @abc.abstractmethod
  def get_vcs_remote(self, repository: Repository) -> VcsHost | None:
    """ Return the interface for interacting with the VCS hosting service. """

  @abc.abstractmethod
  def get_projects(self, repository: Repository) -> list[Project]:
    """ Return the projects of this repository. """


class ProjectHandlerPlugin(abc.ABC):
  """ A plugin that implements the core functionality of a project. Project handlers are intermediate layers between
  the Slam tooling and the actual project configuration, allowing different types of configurations to be adapted and
  used with Slam. """

  ENTRYPOINT = 'slam.plugins.project'

  @abc.abstractmethod
  def matches_project(self, project: Project) -> bool:
    """ Return `True` if the handler is able to provide data for the given project. """

  @abc.abstractmethod
  def get_dist_name(self, project: Project) -> str | None:
    """ Return the distribution name for the project. """

  @abc.abstractmethod
  def get_readme(self, project: Project) -> str | None:
    """ Return the readme file configured for the project. """

  @abc.abstractmethod
  def get_packages(self, project: Project) -> list[Package] | None:
    """ Return a list of packages for the project. Return `None` to indicate that the project is expected to
    not contain any packages. """

  @abc.abstractmethod
  def get_dependencies(self, project: Project) -> Dependencies:
    """ Return the dependencies of the project. """


class CheckPlugin(abc.ABC):
  """ This plugin type can be implemented to add custom checks to the `shut check` command. Note that checks will
  be grouped and their names prefixed with the plugin name, so that name does not need to be included in the name
  of the returned checks. """

  ENTRYPOINT = 'slam.plugins.check'

  def get_project_checks(self, project: Project) -> t.Iterable[Check]:
    return []

  def get_application_checks(self, app: Application) -> t.Iterable[Check]:
    return []


class ReleasePlugin(abc.ABC):
  """ This plugin type provides additional references to the project's version number allowing `slam release` to
  update these references to a new version number.
  """

  ENTRYPOINT = 'slam.plugins.release'

  app: Application
  io: IO

  def get_version_refs(self, project: Project) -> list[VersionRef]:
    """ Return a list of occurrences of the project version. """

    return []

  def create_release(self, project: Project, target_version: str, dry: bool) -> t.Sequence[Path]:
    """ Gives the plugin a chance to perform an arbitrary action after all version references have been bumped,
    being informed of the target version. If *dry* is `True`, the plugin should only act as if it was performing
    its usual actions but not commit the changes to disk. It should return the list of files that it modifies
    or would have modified. """

    return []


class VersionIncrementingRulePlugin(abc.ABC):
  """ This plugin type can be implemented to provide rules accepted by the `slam release <rule>` command to "bump" an
  existing version number to another. The builtin rules implemented in #slam.ext.version_incrementing_rules.
  """

  ENTRYPOINT = 'slam.plugins.version_incrementing_rule'

  def increment_version(self, version: Version) -> Version: ...


@union(union.Subtypes.entrypoint('slam.plugins.vcs_host_provider'))
class VcsHostProvider(abc.ABC):
  """ A plugin class for providing a VCS remote for changelog and release management that can be defined in
  the Slam configuration. """

  @abc.abstractmethod
  def get_vcs_host(self, project: Project) -> VcsHost: ...


class VcsHostDetector(abc.ABC):
  """ This plugin type is used to automatically detect a matching #VcsHost. """

  ENTRYPOINT = 'slam.plugins.vcs_host_detector'

  @abc.abstractmethod
  def detect_vcs_host(self, project: Project) -> VcsHost | None: ...


class ChangelogUpdateAutomationPlugin(abc.ABC):
  """ This plugin type can be used with the `slam changelog update-pr -use <plugin_name>` option. It provides all the
  details derivable from the environment (e.g. environment variables available from CI builds) that can be used to
  detect which changelog entries have been added in a pull request, the pull request URL and the means to publish
  the changes back to the original repository.
  """

  ENTRYPOINT = 'slam.plugins.changelog_update_automation'

  io: IO

  @abc.abstractmethod
  def initialize(self) -> None: ...

  @abc.abstractmethod
  def get_base_ref(self) -> str: ...

  @abc.abstractmethod
  def get_pr(self) -> str: ...

  @abc.abstractmethod
  def publish_changes(self, changed_files: list[Path]) -> None: ...
