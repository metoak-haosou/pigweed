# Copyright 2020 The Pigweed Authors
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.
"""Stores the environment changes necessary for Pigweed."""

import contextlib
import os
import re


class BadNameType(TypeError):
    pass


class BadValueType(TypeError):
    pass


class EmptyValue(ValueError):
    pass


class NewlineInValue(TypeError):
    pass


class BadVariableName(ValueError):
    pass


class UnexpectedAction(ValueError):
    pass


class _Action(object):  # pylint: disable=useless-object-inheritance
    def unapply(self, env, orig_env):  # pylint: disable=no-self-use
        del env, orig_env  # Only used in _VariableAction and subclasses.


class _VariableAction(_Action):
    # pylint: disable=redefined-builtin,too-few-public-methods
    # pylint: disable=keyword-arg-before-vararg
    def __init__(self, name, value, allow_empty_values=False, *args, **kwargs):
        super(_VariableAction, self).__init__(*args, **kwargs)
        self.name = name
        self.value = value
        self.allow_empty_values = allow_empty_values

        self._check()

    def _check(self):
        try:
            # In python2, unicode is a distinct type.
            valid_types = (str, unicode)  # pylint: disable=undefined-variable
        except NameError:
            valid_types = (str, )

        if not isinstance(self.name, valid_types):
            raise BadNameType('variable name {!r} not of type str'.format(
                self.name))
        if not isinstance(self.value, valid_types):
            raise BadValueType('{!r} value {!r} not of type str'.format(
                self.name, self.value))

        # Empty strings as environment variable values have different behavior
        # on different operating systems. Just don't allow them.
        if not self.allow_empty_values and self.value == '':
            raise EmptyValue('{!r} value {!r} is the empty string'.format(
                self.name, self.value))

        # Many tools have issues with newlines in environment variable values.
        # Just don't allow them.
        if '\n' in self.value:
            raise NewlineInValue('{!r} value {!r} contains a newline'.format(
                self.name, self.value))

        if not re.match(r'^[A-Z_][A-Z0-9_]*$', self.name, re.IGNORECASE):
            raise BadVariableName('bad variable name {!r}'.format(self.name))

    def unapply(self, env, orig_env):
        if self.name in orig_env:
            env[self.name] = orig_env[self.name]
        else:
            env.pop(self.name, None)


class _Set(_VariableAction):
    def write(self, outs, windows=(os.name == 'nt')):
        if windows:
            outs.write('set {name}={value}\n'.format(**vars(self)))
        else:
            outs.write(
                '{name}="{value}"\nexport {name}\n'.format(**vars(self)))

    def apply(self, env):
        env[self.name] = self.value


class _Clear(_VariableAction):
    def __init__(self, *args, **kwargs):
        kwargs['value'] = ''
        kwargs['allow_empty_values'] = True
        super(_Clear, self).__init__(*args, **kwargs)

    def write(self, outs, windows=(os.name == 'nt')):
        if windows:
            outs.write('set {name}=\n'.format(**vars(self)))
        else:
            outs.write('unset {name}\n'.format(**vars(self)))

    def apply(self, env):
        if self.name in env:
            del env[self.name]


class _Remove(_VariableAction):
    def __init__(self, name, value, pathsep, *args, **kwargs):
        super(_Remove, self).__init__(name, value, *args, **kwargs)
        self._pathsep = pathsep

    def write(self, outs, windows=(os.name == 'nt')):
        if windows:
            outs.write(':: Remove\n::   {value}\n:: from\n::   {name}\n'
                       ':: before adding it back.\n'
                       'set {name}=%{name}:{pathsep}{value}=%\n'
                       'set {name}=%{name}:{value}{pathsep}=%\n'.format(
                           name=self.name,
                           value=self.value,
                           pathsep=self._pathsep))

        else:
            outs.write('# Remove \n#   {value}\n# from\n#   {name}\n# before '
                       'adding it back.\n'
                       '{name}=$(echo "${name}"'
                       ' | sed "s/{pathsep}{escvalue}{pathsep}/{pathsep}/g;"'
                       ' | sed "s/^{escvalue}{pathsep}//g;"'
                       ' | sed "s/{pathsep}{escvalue}$//g;"'
                       ')\nexport {name}\n'.format(name=self.name,
                                                   value=self.value,
                                                   escvalue=self.value.replace(
                                                       '/', '\\/'),
                                                   pathsep=self._pathsep))

    def apply(self, env):
        env[self.name] = env[self.name].replace(
            '{}{}'.format(self.value, self._pathsep), '')
        env[self.name] = env[self.name].replace(
            '{}{}'.format(self._pathsep, self.value), '')


class BadVariableValue(ValueError):
    pass


def _append_prepend_check(action):
    if '=' in action.value:
        raise BadVariableValue('"{}" contains "="'.format(action.value))


class _Prepend(_VariableAction):
    def __init__(self, name, value, join, *args, **kwargs):
        super(_Prepend, self).__init__(name, value, *args, **kwargs)
        self._join = join

    def write(self, outs, windows=(os.name == 'nt')):
        if windows:
            outs.write('set {name}={value}\n'.format(
                name=self.name,
                value=self._join(self.value, '%{}%'.format(self.name))))
        else:
            outs.write('{name}="{value}"\nexport {name}\n'.format(
                name=self.name, value=self._join(self.value, '$' + self.name)))

    def apply(self, env):
        env[self.name] = self._join(self.value, env.get(self.name, ''))

    def _check(self):
        super(_Prepend, self)._check()
        _append_prepend_check(self)


class _Append(_VariableAction):
    def __init__(self, name, value, join, *args, **kwargs):
        super(_Append, self).__init__(name, value, *args, **kwargs)
        self._join = join

    def write(self, outs, windows=(os.name == 'nt')):
        if windows:
            outs.write('set {name}={value}\n'.format(
                name=self.name,
                value=self._join('%{}%'.format(self.name), self.value)))
        else:
            outs.write('{name}="{value}"\nexport {name}\n'.format(
                name=self.name, value=self._join('$' + self.name, self.value)))

    def apply(self, env):
        env[self.name] = self._join(env.get(self.name, ''), self.value)

    def _check(self):
        super(_Append, self)._check()
        _append_prepend_check(self)


class BadEchoValue(ValueError):
    pass


class _Echo(_Action):
    def __init__(self, value, newline, *args, **kwargs):
        # These values act funny on Windows.
        if value.lower() in ('off', 'on'):
            raise BadEchoValue(value)
        super(_Echo, self).__init__(*args, **kwargs)
        self.value = value
        self._newline = newline

    def write(self, outs, windows=(os.name == 'nt')):
        # POSIX shells parse arguments and pass to echo, but Windows seems to
        # pass the command line as is without parsing, so quoting is wrong.
        if windows:
            if self._newline:
                outs.write('echo {}\n'.format(self.value))
            else:
                outs.write('<nul set /p="{}"\n'.format(self.value))
        else:
            # TODO(mohrr) use shlex.quote().
            outs.write('if [ -z "${PW_ENVSETUP_QUIET:-}" ]; then\n')
            if self._newline:
                outs.write('  echo "{}"\n'.format(self.value))
            else:
                outs.write('  echo -n "{}"\n'.format(self.value))
            outs.write('fi\n')

    def apply(self, env):  # pylint: disable=no-self-use
        del env  # Unused.


class _Comment(_Action):
    def __init__(self, value, *args, **kwargs):
        super(_Comment, self).__init__(*args, **kwargs)
        self.value = value

    def write(self, outs, windows=(os.name == 'nt')):
        comment_char = '::' if windows else '#'
        for line in self.value.splitlines():
            outs.write('{} {}\n'.format(comment_char, line))

    def apply(self, env):  # pylint: disable=no-self-use
        del env  # Unused.


class _BlankLine(_Action):
    def write(  # pylint: disable=no-self-use
        self, outs, windows=(os.name == 'nt')):
        del windows  # Unused.
        outs.write('\n')

    def apply(self, env):  # pylint: disable=no-self-use
        del env  # Unused.


# TODO(mohrr) remove disable=useless-object-inheritance once in Python 3.
# pylint: disable=useless-object-inheritance
class Environment(object):
    """Stores the environment changes necessary for Pigweed.

    These changes can be accessed by writing them to a file for bash-like
    shells to source or by using this as a context manager.
    """
    def __init__(self, *args, **kwargs):
        pathsep = kwargs.pop('pathsep', os.pathsep)
        windows = kwargs.pop('windows', os.name == 'nt')
        allcaps = kwargs.pop('allcaps', windows)
        super(Environment, self).__init__(*args, **kwargs)
        self._actions = []
        self._pathsep = pathsep
        self._windows = windows
        self._allcaps = allcaps

    def _join(self, *args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = args[0]
        return self._pathsep.join(args)

    def normalize_key(self, name):
        if self._allcaps:
            try:
                return name.upper()
            except AttributeError:
                # The _Action class has code to handle incorrect types, so
                # we just ignore this error here.
                pass
        return name

    # A newline is printed after each high-level operation. Top-level
    # operations should not invoke each other (this is why _remove() exists).

    def set(self, name, value):
        name = self.normalize_key(name)
        self._actions.append(_Set(name, value))
        self._blankline()

    def clear(self, name):
        name = self.normalize_key(name)
        self._actions.append(_Clear(name))
        self._blankline()

    def _remove(self, name, value):
        """Remove a value from a variable."""

        name = self.normalize_key(name)
        if self.get(name, None):
            self._actions.append(_Remove(name, value, self._pathsep))

    def remove(self, name, value):
        self._remove(name, value)
        self._blankline()

    def append(self, name, value):
        """Add a value to the end of a variable. Rarely used, see prepend()."""

        name = self.normalize_key(name)
        if self.get(name, None):
            self._remove(name, value)
            self._actions.append(_Append(name, value, self._join))
        else:
            self._actions.append(_Set(name, value))
        self._blankline()

    def prepend(self, name, value):
        """Add a value to the beginning of a variable."""

        name = self.normalize_key(name)
        if self.get(name, None):
            self._remove(name, value)
            self._actions.append(_Prepend(name, value, self._join))
        else:
            self._actions.append(_Set(name, value))
        self._blankline()

    def echo(self, value, newline=True):
        self._actions.append(_Echo(value, newline))
        self._blankline()

    def comment(self, comment):
        self._actions.append(_Comment(comment))
        self._blankline()

    def _blankline(self):
        self._actions.append(_BlankLine())

    def write(self, outs):
        """Writes a shell init script to outs."""
        if self._windows:
            outs.write('@echo off\n')

        for action in self._actions:
            action.write(outs, windows=self._windows)

        if not self._windows:
            outs.write(
                '# This should detect bash and zsh, which have a hash \n'
                '# command that must be called to get it to forget past \n'
                '# commands. Without forgetting past commands the $PATH \n'
                '# changes we made may not be respected.\n'
                'if [ -n "${BASH:-}" -o -n "${ZSH_VERSION:-}" ] ; then\n'
                '    hash -r\n'
                'fi\n')

    @contextlib.contextmanager
    def __call__(self, export=True):
        """Set environment as if this was written to a file and sourced.

        Within this context os.environ is updated with the environment
        defined by this object. If export is False, os.environ is not updated,
        but in both cases the updated environment is yielded.

        On exit, previous environment is restored. See contextlib documentation
        for details on how this function is structured.

        Args:
          export(bool): modify the environment of the running process (and
            thus, its subprocesses)

        Yields the new environment object.
        """
        try:
            if export:
                orig_env = os.environ.copy()
                env = os.environ
            else:
                env = os.environ.copy()

            for action in self._actions:
                action.apply(env)

            yield env

        finally:
            if export:
                for action in self._actions:
                    action.unapply(env=os.environ, orig_env=orig_env)

    def get(self, key, default=None):
        """Get the value of a variable within context of this object."""
        key = self.normalize_key(key)
        with self(export=False) as env:
            return env.get(key, default)

    def __getitem__(self, key):
        """Get the value of a variable within context of this object."""
        key = self.normalize_key(key)
        with self(export=False) as env:
            return env[key]
