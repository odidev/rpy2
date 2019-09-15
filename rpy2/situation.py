"""
This module is currently primarily intended to be used as a script.
It will print information about the rpy2's environment (Python version,
R version, rpy2 version, etc...).
"""

import argparse
import enum
import os
import shlex
import subprocess
import sys
import warnings

try:
    import rpy2  # noqa:F401
    has_rpy2 = True
except ImportError:
    has_rpy2 = False


class CFFI_MODE(enum.Enum):
    API = 'API'
    ABI = 'ABI'
    BOTH = 'BOTH'


def get_cffi_mode(default=CFFI_MODE.ABI):
    cffi_mode = os.environ.get('RPY2_CFFI_MODE', '')
    for m in (CFFI_MODE.API, CFFI_MODE.ABI, CFFI_MODE.BOTH):
        if cffi_mode.upper() == m.value:
            return m
    return default


def assert_python_version():
    if not (sys.version_info[0] >= 3 and sys.version_info[1] >= 3):
        raise RuntimeError(
            "Python >=3.3 is required to run rpy2")


def r_version_from_subprocess():
    try:
        tmp = subprocess.check_output(("R", "--version"))
    except Exception:  # FileNotFoundError, WindowsError, etc
        return None
    r_version = tmp.decode('ascii', 'ignore').split(os.linesep)
    if r_version[0].startswith("WARNING"):
        r_version = r_version[1]
    else:
        r_version = r_version[0].strip()
    return r_version


def r_home_from_subprocess() -> str:
    """Return the R home directory from calling 'R RHOME'."""
    try:
        tmp = subprocess.check_output(('R', 'RHOME'), universal_newlines=True)
    except Exception:  # FileNotFoundError, WindowsError, etc
        return
    r_home = tmp.split(os.linesep)
    if r_home[0].startswith('WARNING'):
        r_home = r_home[1]
    else:
        r_home = r_home[0].strip()
    return r_home


def r_home_from_registry() -> str:
    """Return the R home directory from the Windows Registry."""
    try:
        import winreg
    except ImportError:
        import _winreg as winreg
    try:
        hkey = winreg.OpenKeyEx(winreg.HKEY_LOCAL_MACHINE,
                                'Software\\R-core\\R',
                                0, winreg.KEY_QUERY_VALUE)
        r_home = winreg.QueryValueEx(hkey, 'InstallPath')[0]
        winreg.CloseKey(hkey)
    except Exception:  # FileNotFoundError, WindowsError, etc
        return None
    if sys.version_info[0] == 2:
        r_home = r_home.encode(sys.getfilesystemencoding())
    return r_home


def get_rlib_path(r_home: str, system: str) -> str:
    """Get the path for the R shared library."""
    if system == 'Linux':
        lib_path = os.path.join(r_home, 'lib', 'libR.so')
    elif system == 'Darwin':
        lib_path = os.path.join(r_home, 'lib', 'libR.dylib')
    else:
        raise ValueError('The system "%s" is not supported.')
    return lib_path


def get_r_home() -> str:
    """Get R's home directory (aka R_HOME).

    If an environment variable R_HOME is found it is returned,
    and if none is found it is trying to get it from an R executable
    in the PATH. On Windows, a third last attempt is made by trying
    to obtain R_HOME from the registry. If all attempt are unfruitful,
    None is returned.
    """

    r_home = os.environ.get('R_HOME')

    if not r_home:
        r_home = r_home_from_subprocess()
    if not r_home and sys.platform == 'win32':
        r_home = r_home_from_registry()
    return r_home


def get_r_exec(r_home: str) -> str:
    """Get the path of the R executable/binary.

    :param: R HOME directory
    :return: Path to the R executable/binary"""
    if sys.platform == 'win32' and '64 bit' in sys.version:
        r_exec = os.path.join(r_home, 'bin', 'x64', 'R')
    else:
        r_exec = os.path.join(r_home, 'bin', 'R')
    return r_exec


def _get_r_cmd_config(r_home: str, about: str, allow_empty=False):
    """Get the output of calling 'R CMD CONFIG <about>'.

    :param r_home: R HOME directory
    :param about: argument passed to the command line 'R CMD CONFIG'
    :param allow_empty: allow the output to be empty
    :return: a tuple (lines of output)"""
    r_exec = get_r_exec(r_home)
    cmd = (r_exec, 'CMD', 'config', about)
    print(subprocess.list2cmdline(cmd))
    output = subprocess.check_output(cmd,
                                     universal_newlines=True)
    output = output.split(os.linesep)
    # Twist if 'R RHOME' spits out a warning
    if output[0].startswith('WARNING'):
        warnings.warn('R emitting a warning: %s' % output[0])
        output = output[1:]
    return output


_R_LIBS = ('LAPACK_LIBS', 'BLAS_LIBS')
_R_FLAGS = ('--ldflags', '--cppflags')


def get_r_flags(r_home: str, flags: str):
    """Get the parsed output of calling 'R CMD CONFIG <about>'.

    Returns a tuple (parsed_args, unknown_args), with parsed_args
    having the attribute `l`, 'L', and 'I'."""

    assert flags in _R_FLAGS

    parser = argparse.ArgumentParser()
    parser.add_argument('-I', action='append')
    parser.add_argument('-L', action='append')
    parser.add_argument('-l', action='append')

    res = shlex.split(
        ' '.join(
            _get_r_cmd_config(r_home, flags,
                              allow_empty=False)))
    return parser.parse_known_args(res)


def get_r_libs(r_home: str, libs: str):
    return _get_r_cmd_config(r_home, libs, allow_empty=True)


class CExtensionOptions(object):
    """Options to compile C extensions."""

    def __init__(self):
        self.extra_link_args = []
        self.extra_compile_args = []
        self.include_dirs = []
        self.libraries = []
        self.library_dirs = []

    def add_include(self, args, unknown):
        """Add include directories.

        :param args: args as returned by get_r_flags().
        :param unknown: unknown arguments a returned by get_r_flags()."""
        if args.I is None:
            warnings.warn('No include specified')
        else:
            self.include_dirs.extend(args.I)
        self.extra_compile_args.extend(unknown)

    def add_lib(self, args, unknown, ignore=('R', )):
        """Add libraries.

        :param args: args as returned by get_r_flags().
        :param unknown: unknown arguments a returned by get_r_flags()."""
        if args.L is None:
            if args.l is None:
                # hmmm... no libraries at all
                warnings.warn('No libraries as -l arguments to the compiler.')
            else:
                self.libraries.extend([x for x in args.l if x not in ignore])
        else:
            self.library_dirs.extend(args.L)
            self.libraries.extend(args.l)
        self.extra_link_args.extend(unknown)


def _make_bold(text):
    return '%s%s%s' % ('\033[1m', text, '\033[0m')


def iter_info():

    yield _make_bold('rpy2 version:')
    if has_rpy2:
        # TODO: the repeated import is needed, without which Python (3.6)
        #   raises an UnboundLocalError (local variable reference before
        #   assignment).
        import rpy2  # noqa: F811
        yield rpy2.__version__
    else:
        yield 'rpy2 cannot be imported'

    yield _make_bold('Python version:')
    yield sys.version
    if not (sys.version_info[0] == 3 and sys.version_info[1] >= 5):
        yield '*** rpy2 is primarily designed for Python >= 3.5'

    yield _make_bold("Looking for R's HOME:")

    r_home = os.environ.get('R_HOME')
    yield '    Environment variable R_HOME: %s' % r_home

    r_home = r_home_from_subprocess()
    yield '    Calling `R RHOME`: %s' % r_home

    if sys.platform == 'win32':
        r_home = r_home_from_registry()
        yield '    InstallPath in the registry: %s' % r_home

    if has_rpy2:
        try:
            import rpy2.rinterface_lib.openrlib
            rlib_status = 'OK'
        except ImportError as ie:
            rlib_status = '*** Error while loading: %s ***' % str(ie)
    else:
        rlib_status = '*** rpy2 is not installed'

    yield _make_bold("R version:")
    yield '    In the PATH: %s' % r_version_from_subprocess()
    yield '    Loading R library from rpy2: %s' % rlib_status

    r_libs = os.environ.get('R_LIBS')
    yield _make_bold('Additional directories to load R packages from:')
    yield r_libs

    yield _make_bold('C extension compilation:')
    c_ext = CExtensionOptions()
    c_ext.add_lib(*get_r_flags(r_home, '--ldflags'))
    c_ext.add_include(*get_r_flags(r_home, '--cppflags'))
    yield '  include:'
    yield '  %s' % c_ext.include_dirs
    yield '  libraries:'
    yield '  %s' % c_ext.libraries
    yield '  library_dirs:'
    yield '  %s' % c_ext.library_dirs
    yield '  extra_link_args:'
    yield '  %s' % c_ext.extra_link_args


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        'Command-line tool to report the rpy2'
        'environment and help diagnose issues')
    args = parser.parse_args()
    for row in iter_info():
        print(row)
