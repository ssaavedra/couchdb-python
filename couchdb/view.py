#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2007-2008 Christopher Lenz
# All rights reserved.
#
# This software is licensed as described in the file COPYING, which
# you should have received as part of this distribution.

"""Implementation of a view server for functions written in Python."""

from codecs import BOM_UTF8
import logging
import os
import sys
import traceback
from types import FunctionType

from couchdb import json

__all__ = ['main', 'run']
__docformat__ = 'restructuredtext en'

log = logging.getLogger('couchdb.view')


def run(input=sys.stdin, output=sys.stdout):
    r"""CouchDB view function handler implementation for Python.

    :param input: the readable file-like object to read input from
    :param output: the writable file-like object to write output to
    """
    functions = []
    design_docs = {}

    def _writejson(obj):
        obj = json.encode(obj)
        if isinstance(obj, unicode):
            obj = obj.encode('utf-8')
        output.write(obj)
        output.write('\n')
        output.flush()

    def _log(message):
        if not isinstance(message, basestring):
            message = json.encode(message)
        _writejson({'log': message})

    def _parse_python(string):
        string = BOM_UTF8 + string.encode('utf-8')
        globals_ = {}
        try:
            exec string in {'log': _log}, globals_
        except Exception, e:
            log.debug("[PARSEPYTHON]: %s", e, exc_info=True)
            return None

        if len(globals_) != 1:
            return None
        function = globals_.values()[0]
        if type(function) is not FunctionType:
            return None
        return function


    def reset(config=None):
        del functions[:]
        return True

    def add_fun(string):
        try:
            function = _parse_python(string)
        except Exception, e:
            return {'error': {
                'id': 'map_compilation_error',
                'reason': e.args[0]
            }}

        if not function:
            return err

        err = {'error': {
            'id': 'map_compilation_error',
            'reason': 'string must eval to a function '
                      '(ex: "def(doc): return 1")'
        }}
        if type(function) is not FunctionType:
            return err
        functions.append(function)
        return True

    def map_doc(doc):
        results = []
        for function in functions:
            try:
                results.append([[key, value] for key, value in function(doc)])
            except Exception, e:
                log.error('runtime error in map function: %s', e,
                          exc_info=True)
                results.append([])
                _log(traceback.format_exc())
        return results

    def ddoc(operation, docname, doc):
        if operation == 'new':
            design_docs[docname] = doc
            return True

        elif operation in design_docs and len(docname) == 1:
            # We have to work with a design doc's something
            part = design_docs[operation]
            if docname[0] == 'validate_doc_update':
                # We have to validate the document with the validation document in python.
                # So let us parse it first
                try:
                    lambda_ = _parse_python(part[docname[0]])
                except Exception, e:
                    return {'error': {
                        'id': 'validate_doc_update_compilation_error',
                        'reason': e.args[0]
                    }}

                try:
                    retval = lambda_(*doc)
                except Exception, e:
                    # Exception ocurred during validation function. Deny update.
                    log.debug("[DDOC][EVAL]: Exception during validation: %s", e, exc_info=True)
                    retval = { "forbidden": "exception during validation" }
                log.debug("[DDOC][VAL]: Returning value: %s", retval)
                return retval

        else:
            from pprint import pformat
            log.error("[DDOC]: Operation '%s' not understood", operation)
            log.debug("[DDOC]: Argument #1: %s", pformat(docname))
            log.debug("[DDOC]: Argument #2: %s", pformat(doc))
            _writejson({'log': "error in ddoc, operation %s not understood" % operation})

    def reduce(*cmd, **kwargs):
        code = BOM_UTF8 + cmd[0][0].encode('utf-8')
        args = cmd[1]
        globals_ = {}
        try:
            exec code in {'log': _log}, globals_
        except Exception, e:
            log.error('runtime error in reduce function: %s', e,
                      exc_info=True)
            return {'error': {
                'id': 'reduce_compilation_error',
                'reason': e.args[0]
            }}
        err = {'error': {
            'id': 'reduce_compilation_error',
            'reason': 'string must eval to a function '
                      '(ex: "def(keys, values): return 1")'
        }}
        if len(globals_) != 1:
            return err
        function = globals_.values()[0]
        if type(function) is not FunctionType:
            return err

        rereduce = kwargs.get('rereduce', False)
        results = []
        if rereduce:
            keys = None
            vals = args
        else:
            if args:
                keys, vals = zip(*args)
            else:
                keys, vals = [], []
        if function.func_code.co_argcount == 3:
            results = function(keys, vals, rereduce)
        else:
            results = function(keys, vals)
        return [True, [results]]

    def rereduce(*cmd):
        # Note: weird kwargs is for Python 2.5 compat
        return reduce(*cmd, **{'rereduce': True})

    handlers = {'reset': reset, 'add_fun': add_fun, 'map_doc': map_doc,
                'reduce': reduce, 'rereduce': rereduce, 'ddoc': ddoc}

    try:
        while True:
            line = input.readline()
            if not line:
                break
            try:
                cmd = json.decode(line)
                log.debug('Processing %r', cmd)
            except ValueError, e:
                log.error('Error: %s', e, exc_info=True)
                return 1
            else:
                retval = handlers[cmd[0]](*cmd[1:])
                log.debug('Returning  %r', retval)
                _writejson(retval)
    except KeyboardInterrupt:
        return 0
    except Exception, e:
        log.error('Error: %s', e, exc_info=True)
        return 1


_VERSION = """%(name)s - CouchDB Python %(version)s

Copyright (C) 2007 Christopher Lenz <cmlenz@gmx.de>.
"""

_HELP = """Usage: %(name)s [OPTION]

The %(name)s command runs the CouchDB Python view server.

The exit status is 0 for success or 1 for failure.

Options:

  --version             display version information and exit
  -h, --help            display a short help message and exit
  --json-module=<name>  set the JSON module to use ('simplejson', 'cjson',
                        or 'json' are supported)
  --log-file=<file>     name of the file to write log messages to, or '-' to
                        enable logging to the standard error stream
  --debug               enable debug logging; requires --log-file to be
                        specified

Report bugs via the web at <http://code.google.com/p/couchdb-python>.
"""


def main():
    """Command-line entry point for running the view server."""
    import getopt
    from couchdb import __version__ as VERSION

    try:
        option_list, argument_list = getopt.gnu_getopt(
            sys.argv[1:], 'h',
            ['version', 'help', 'json-module=', 'debug', 'log-file=']
        )

        message = None
        for option, value in option_list:
            if option in ('--version'):
                message = _VERSION % dict(name=os.path.basename(sys.argv[0]),
                                      version=VERSION)
            elif option in ('-h', '--help'):
                message = _HELP % dict(name=os.path.basename(sys.argv[0]))
            elif option in ('--json-module'):
                json.use(module=value)
            elif option in ('--debug'):
                log.setLevel(logging.DEBUG)
            elif option in ('--log-file'):
                if value == '-':
                    handler = logging.StreamHandler(sys.stderr)
                    handler.setFormatter(logging.Formatter(
                        ' -> [%(levelname)s] %(message)s'
                    ))
                else:
                    handler = logging.FileHandler(value)
                    handler.setFormatter(logging.Formatter(
                        '[%(asctime)s] [%(levelname)s] %(message)s'
                    ))
                log.addHandler(handler)
        if message:
            sys.stdout.write(message)
            sys.stdout.flush()
            sys.exit(0)

    except getopt.GetoptError, error:
        message = '%s\n\nTry `%s --help` for more information.\n' % (
            str(error), os.path.basename(sys.argv[0])
        )
        sys.stderr.write(message)
        sys.stderr.flush()
        sys.exit(1)

    sys.exit(run())


if __name__ == '__main__':
    main()


# vim: set ts=4 sw=4 sts=4 et :

