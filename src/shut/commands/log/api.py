
import abc

from databind.core.annotations import union

from shut.application import Application
from shut.changelog.changelog_manager import ChangelogValidator


class RemoteDetectorPlugin(abc.ABC):
  """ A plugin class for detecting a VCS remote for changelog and release management. """

  @abc.abstractmethod
  def detect_changelog_validator(self, app: Application) -> ChangelogValidator | None: ...


@union(union.Subtypes.entrypoint('shut.commands.log.config.RemoteProvider'))
class RemoteProvider(abc.ABC):
  """
  A plugin class for providing a VCS remote for changelog and release management that can be defined in
  the Shut configuration.
  """

  @abc.abstractmethod
  def get_changelog_validator(self, app: Application) -> ChangelogValidator: ...
