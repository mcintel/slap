# -*- coding: utf8 -*-
# Copyright (c) 2020 Niklas Rosenstein
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.

import collections
import contextlib
import json
import logging
import os
import posixpath
import re
import textwrap
from typing import Dict, Iterable, List, Optional, TextIO, Tuple

import nr.fs  # type: ignore

from shut.model.package import PackageModel, Include, Exclude
from shut.model.requirements import RequirementsList
from shut.utils.io.virtual import VirtualFiles
from .core import Renderer, register_renderer, VersionRef

logger = logging.getLogger(__name__)

GENERATED_FILE_REMARK = '''
# This file was auto-generated by Shut. DO NOT EDIT
# For more information about Shut, check out https://pypi.org/project/shut/
'''.strip() + '\n'

_ReadmeStatus = collections.namedtuple('ReadmeStatus', 'path,runtime_path,outside')


def _normpath(x):
  return os.path.normpath(x).replace(os.sep, '/')


def _get_readme_content_type(filename: str) -> str:
  return {
    'md': 'text/markdown',
    'rst': 'text/x-rst',
  }.get(nr.fs.getsuffix(filename), 'text/plain')


def _split_section(data, begin_marker, end_marker):
  start = data.find(begin_marker)
  end = data.find(end_marker, start)
  if start >= 0 and end >= 0:
    prefix = data[:start]
    middle = data[start+len(begin_marker):end]
    suffix = data[end+len(end_marker)+1:]
    return prefix, middle, suffix
  return (data, '', '')


@contextlib.contextmanager
def _rewrite_section(fp, data, begin_marker, end_marker):
  """
  Helper to rewrite a section of a file delimited by *begin_marker* and *end_marker*.
  """

  prefix, suffix = _split_section(data, begin_marker, end_marker)[::2]
  fp.write(prefix)
  fp.write(begin_marker + '\n')
  yield fp
  fp.write(end_marker + '\n')
  fp.write(suffix)


class SetuptoolsRenderer(Renderer[PackageModel]):

  #: Begin an end section for the MANIFEST.in file.
  _BEGIN_SECTION = '# This section is auto-generated by Shut. DO NOT EDIT {'
  _END_SECTION = '# }'

  #: These variables are used to format entrypoints in the setup.py file. It
  #: allows the addition of the Python interpreter version to the entrypoint
  #: names.
  _ENTRTYPOINT_VARS = {
    'python-major-version': 'sys.version[0]',
    'python-major-minor-version': 'sys.version[:3]'
  }

  def _render_setup(
    self,
    fp: TextIO,
    package: PackageModel,
  ) -> None:
    metadata = package.get_python_package_metadata()
    install = package.install

    # Write the header/imports.
    fp.write(GENERATED_FILE_REMARK + '\n')
    fp.write('from __future__ import print_function\n')
    if install.hooks.before_install or install.hooks.after_install:
      fp.write('from setuptools.command.install import install as _install_command\n')
    if install.hooks.before_develop or install.hooks.after_develop:
      fp.write('from setuptools.command.develop import develop as _develop_command\n')
    fp.write(textwrap.dedent('''
      import io
      import os
      import setuptools
      import sys
    ''').lstrip())

    # Write hook overrides.
    cmdclass = {}
    if install.hooks.any():
      fp.write('\ninstall_hooks = [\n')
      for hook in package.install_hooks:
        fp.write('  ' + json.dumps(hook.normalize().to_json(), sort_keys=True) + ',\n')
      fp.write(']\n')
      fp.write(textwrap.dedent('''
        def _run_hooks(event):
          import subprocess, shlex, os
          def _shebang(fn):
            with open(fn) as fp:
              line = fp.readline()
              if line.startswith('#'):
                return shlex.split(line[1:].strip())
              return []
          for hook in install_hooks:
            if not hook['event'] or hook['event'] == event:
              command = hook['command']
              if command[0].endswith('.py') or 'python' in _shebang(command[0]):
                command.insert(0, sys.executable)
              env = os.environ.copy()
              env['SHUT_INSTALL_HOOK_EVENT'] = event
              res = subprocess.call(command, env=env)
              if res != 0:
                raise RuntimeError('command {!r} returned exit code {}'.format(command, res))
      '''))
    if install.hooks.after_install or install.hooks.before_install:
      fp.write(textwrap.dedent('''
        class install_command(_install_command):
          def run(self):
            _run_hooks('install')
            super(install_command, self).run()
            _run_hooks('post-install')
      '''))
      cmdclass['install'] = 'install_command'
    if install.hooks.before_develop or install.hooks.after_develop:
      fp.write(textwrap.dedent('''
        class develop_command(_develop_command):
          def run(self):
            _run_hooks('develop')
            super(develop_command, self).run()
            _run_hooks('post-develop')
      '''))
      cmdclass['develop'] = 'develop_command'

    license_file = package.get_license_file(True)
    license_file = os.path.normpath(license_file) if license_file else None
    license_file = os.path.relpath(os.path.abspath(license_file), package.get_directory()) if license_file else None
    if license_file and license_file.startswith(os.pardir + os.sep):
      # We need to copy the license from the monorepo.
      self._render_temp_file_copy_function(fp)
      license_src = os.path.relpath(package.get_license_file(True), package.get_directory())
      license_dst = os.path.basename(license_src)
      fp.write('\n_tempcopy({!r}, {!r})\n'.format(license_src, license_dst))

    readme_file, long_description_expr = self._render_readme_code(fp, package)

    # Write the install requirements.
    fp.write('\n')
    self._render_requirements(fp, 'requirements', package.requirements)

    if package.test_requirements:
      self._render_requirements(fp, 'test_requirements', package.test_requirements)
      tests_require = 'test_requirements'
    else:
      tests_require = '[]'

    if package.extra_requirements:
      fp.write('extras_require = {}\n')
      for key, value in package.extra_requirements.items():
        self._render_requirements(fp, 'extras_require[{!r}]'.format(key), value)
      extras_require = 'extras_require'
    else:
      extras_require = '{}'

    exclude_packages = []
    for pkg in package.exclude:
      exclude_packages.append(pkg)
      exclude_packages.append(pkg + '.*')

    if metadata.is_single_module:
      packages_args = '  py_modules = [{!r}],'.format(package.get_modulename())
    else:
      packages_args = '  packages = setuptools.find_packages({src_directory!r}, {exclude_packages!r}),'.format(
        src_directory=package.source_directory,
        exclude_packages=exclude_packages)

    # Find the requirement on Python itself.
    python_requirement = package.get_python_requirement()
    if python_requirement:
      python_requires_expr = repr(python_requirement.version.to_setuptools() if python_requirement else None)
      if package.is_universal():
        # TODO: We still need to find a good way to convert a Requirement using ORs (|) to setuptools format.
        python_requires_expr = 'None,  # {}'.format(python_requires_expr)
    else:
      python_requires_expr = 'None'

    # TODO: data_files/package_data
    # TODO: py.typed must be included in package_data (or include_package_data=True)
    data_files = '[]'

    # MyPy cannot find PEP-561 compatible packages without zip_safe=False.
    # See https://mypy.readthedocs.io/en/latest/installed_packages.html#making-pep-561-compatible-packages
    zip_safe = not package.typed

    # Write the setup function.
    fp.write(textwrap.dedent('''
      setuptools.setup(
        name = {name!r},
        version = {version!r},
        author = {author_name!r},
        author_email = {author_email!r},
        description = {description!r},
        long_description = {long_description_expr},
        long_description_content_type = {long_description_content_type!r},
        url = {url!r},
        license = {license!r},
      {packages_args}
        package_dir = {{'': {src_directory!r}}},
        include_package_data = {include_package_data!r},
        install_requires = requirements,
        extras_require = {extras_require},
        tests_require = {tests_require},
        python_requires = {python_requires_expr},
        data_files = {data_files},
        entry_points = {entry_points},
        cmdclass = {cmdclass},
        keywords = {keywords!r},
        classifiers = {classifiers!r},
        zip_safe = {zip_safe!r},
    ''').rstrip().format(
      name=package.name,
      version=str(package.get_version()),
      packages_args=packages_args,
      author_name=package.get_author().name if package.get_author() else None,
      author_email=package.get_author().email if package.get_author() else None,
      url=package.get_url(),
      license=package.get_license(),
      description=package.description.replace('\n\n', '%%%%').replace('\n', ' ').replace('%%%%', '\n').strip(),
      long_description_expr=long_description_expr,
      long_description_content_type=_get_readme_content_type(readme_file) if readme_file else None,
      extras_require=extras_require,
      tests_require=tests_require,
      python_requires_expr=python_requires_expr,
      src_directory=package.source_directory,
      include_package_data=True,#package.package_data != [],
      data_files=data_files,
      entry_points=self._render_entrypoints(package.entrypoints),
      cmdclass = '{' + ', '.join('{!r}: {}'.format(k, v) for k, v in cmdclass.items()) + '}',
      keywords = package.keywords,
      classifiers = package.classifiers,
      zip_safe=zip_safe,
    ))

    if package.is_universal():
      fp.write(textwrap.dedent('''
          options = {
            'bdist_wheel': {
              'universal': True,
            },
          },
        )
      '''))
    else:
      fp.write('\n)\n')

  def _render_entrypoints(self, entrypoints: Dict[str, List[str]]) -> None:
    if not entrypoints:
      return '{}'
    lines = ['{']
    for key, value in entrypoints.items():
      lines.append('    {!r}: ['.format(key))
      for item in value:
        item = repr(item)
        args = []
        for varname, expr in self._ENTRTYPOINT_VARS.items():
          varname = '{{' + varname + '}}'
          if varname in item:
            item = item.replace(varname, '{' + str(len(args)) + '}')
            args.append(expr)
        if args:
          item += '.format(' + ', '.join(args) + ')'
        lines.append('      ' + item.strip() + ',')
      lines.append('    ],')
    lines[-1] = lines[-1][:-1]
    lines.append('  }')
    return '\n'.join(lines)

  @staticmethod
  def _format_reqs(reqs: RequirementsList, level: int = 0) -> List[str]:
    indent = '  ' * (level + 1)
    reqs = [r for r in reqs.reqs() if r.package != 'python']
    if not reqs:
      return '[]'
    return '[\n' + ''.join(indent + '{!r},\n'.format(x.to_setuptools()) for x in reqs if x.package != 'python') + ']'

  def _render_requirements(self, fp: TextIO, target: str, requirements: RequirementsList):
    fp.write('{} = {}\n'.format(target, self._format_reqs(requirements)))

  def _get_readme_status(self, package: PackageModel) -> Optional[_ReadmeStatus]:
    """
    Returns some information on the readme for a package. The readme can be located outside
    of the package directory, but that needs to be handled special in various cases.
    """

    readme = package.get_readme_file()
    if not readme:
      return None

    # Make sure the readme is relative (we need it relative either way).
    readme = os.path.relpath(readme, package.get_directory())

    # If the readme file is _not_ inside the package directory, the setup.py will
    # temporarily copy it. The filename at setup time is thus just the readme's
    # base filename.
    is_inside = nr.fs.issub(readme)
    if is_inside:
      readme_relative_path = readme
    else:
      readme_relative_path = os.path.basename(readme)

    return _ReadmeStatus(readme, readme_relative_path, not is_inside)

  def _render_temp_file_copy_function(self, fp: TextIO) -> None:
    """
    Renders a Python function called `_tempcopy()` into *fp*. The function can be used to
    temporarily copy a file for the runtime of the setup file. If the destination file
    already exists, nothing will be done.
    """

    if hasattr(fp, '_SetuptoolsRenderer_tempcopy_rendered'):
      return
    fp._SetuptoolsRenderer_tempcopy_rendered = True

    # TODO(NiklasRosenstein): make sure this gets rendered into the file only once.

    fp.write('\n')
    fp.write(textwrap.dedent('''
      def _tempcopy(src, dst):
        import atexit, shutil
        if not os.path.isfile(dst):
          if not os.path.isfile(src):
            print('warning: source file "{}" for destination "{}" does not exist'.format(src, dst))
            return
          shutil.copyfile(src, dst)
          atexit.register(lambda: os.remove(dst))
    ''').lstrip())
    fp.write('\n')

  def _render_readme_code(self, fp: TextIO, package: PackageModel) -> Tuple[Optional[str], Optional[str]]:
    """
    Renders code for the setup.py file, creating a `long_description` variable. If
    a readme file is present or explicitly specified in *package*, that readme file
    will be read for the setup.

    The readme file may be locatated outside of the packages' directory. In this case,
    the setup.py file will temporarily copy it into the package root directory during
    the setup.

    Returns the Python expression to pass into the `long_description` field of the
    #setuptools.setup() call.
    """

    readme = self._get_readme_status(package)
    if not readme:
      return None, None

    fp.write('\nreadme_file = {!r}\n'.format(readme.runtime_path))

    if readme.outside:
      # Copy the relative README file if it exists.
      self._render_temp_file_copy_function(fp)
      fp.write('_tempcopy({!r}, readme_file)\n'.format(readme.path))

    # Read the contents of the file into the "long_description" variable.
    fp.write(textwrap.dedent('''
      if os.path.isfile(readme_file):
        with io.open(readme_file, encoding='utf8') as fp:
          long_description = fp.read()
      else:
        print("warning: file \\"{}\\" does not exist.".format(readme_file), file=sys.stderr)
        long_description = None
    ''').lstrip())

    return readme.path, 'long_description'

  def _render_manifest_in(self, fp: TextIO, current: TextIO, package: PackageModel) -> None:
    """
    Modifies a `MANIFEST.in` file in place, ensuring that the automatically generatd content
    is up to date (or added if it didn't exist before).
    """

    files = [
      package.filename,
      package.get_py_typed_file(),
      package.get_license_file(True),
    ]

    readme = self._get_readme_status(package)
    if readme:
      files.append(readme.path)

    manifest = [
      os.path.relpath(os.path.abspath(f), package.get_directory())
      for f in files
      if f
    ]

    # Paths outside of the package directory are copied into the package directory
    # on setup.py.
    manifest = [
      os.path.basename(f) if f.startswith(os.pardir + os.sep) else f
      for f in manifest
    ]

    manifest = ['include ' + s for s in manifest]
    metadata = package.get_python_package_metadata()
    for entry in package.package_data:
      if isinstance(entry, Include):
        verb = 'include'
        path = entry.include
      elif isinstance(entry, Exclude):
        verb = 'exclude'
        path = entry.exclude
      else:
        raise RuntimeError(f'unexpected package_data entry: {entry!r}')
      path = posixpath.join(package.source_directory, package.get_modulename().replace('.', '/'), path)
      manifest.append(f'{verb} {path}')

    markers = (self._BEGIN_SECTION, self._END_SECTION)
    with _rewrite_section(fp, current.read() if current else '', *markers):
      for entry in manifest:
        fp.write(entry + '\n')

  # Renderer[PackageModel] Overrides

  def get_files(self, files: VirtualFiles, package: PackageModel) -> None:
    files.add_dynamic('setup.py', self._render_setup, package)
    files.add_dynamic('MANIFEST.in', self._render_manifest_in, package, inplace=True)

    if package.typed:
      directory = package.get_python_package_metadata().package_directory
      files.add_static(os.path.join(directory, 'py.typed'), '')

    if package.has_vendored_requirements():
      logger.info('package "%s" has vendored requirements which will prevent it from '
        'being published.', package.name)

  def get_version_refs(self, package: PackageModel) -> Iterable[VersionRef]:
    def _regex_refs(filename: Optional[str], regex: str) -> Iterable[VersionRef]:
      if filename and os.path.isfile(filename):
        with open(filename) as fp:
          text = fp.read()
          match = re.search(regex, text, re.M)
          if match:
            yield VersionRef(filename, match.start(1), match.end(1), match.group(1))

    filename = os.path.join(package.get_directory(), 'setup.py')
    yield from _regex_refs(filename, r'^\s*version\s*=\s*[\'"]([^\'"]+)[\'"]')

    filename = package.get_python_package_metadata().filename
    yield from _regex_refs(filename, r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]')


register_renderer(PackageModel, SetuptoolsRenderer)
