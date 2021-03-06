#!/usr/bin/env python
# -*- Mode: Python; tab-width: 4; indent-tabs-mode: nil; -*- 
# vim:set ft=python ts=4 sw=4 sts=4 autoindent:

'''
Entry for cgi calls to the brat application. This is a simple wrapper that
only imports a bare minimum and ensures that the web-based UI gets a proper
response even when the server crashes. If in debug mode it returns any errors
that occur using the established messaging API.

This file should stay compatible with Python 2.3 and upwards until it has done
the version checking in order to assure that we can return sensible results
to the user and administrators.

Author:     Pontus  Stenetorp   <pontus is s u tokyo ac jp>
Version:    2010-02-07
'''

from cgi import FieldStorage
from logging import info as log_info
from os import environ
from os.path import dirname
from os.path import join as path_join
from sys import path as sys_path
from sys import version_info

sys_path.append(path_join(dirname(__file__), 'server/lib/simplejson-2.1.5'))
sys_path.append(path_join(dirname(__file__), 'server/src'))

from common import ProtocolError, NoPrintJSONError

from session import get_session

### Constants
# This handling of version_info is strictly for backwards compability
PY_VER_STR = '%d.%d.%d-%s-%d' % tuple(version_info)
REQUIRED_PY_VERSION_MAJOR = 2
REQUIRED_PY_VERSION_MINOR = 5
INVALID_PY_JSON = '''
{
  "messages": [
    [
      "Incompatible Python version (%s), %d.%d or above is supported",
      "error",
      -1
    ]
  ]
}
''' % (PY_VER_STR, REQUIRED_PY_VERSION_MAJOR, REQUIRED_PY_VERSION_MINOR)
CONF_FNAME = 'config.py'
CONF_TEMPLATE_FNAME = 'config_template.py'
###

def _miss_var_msg(var):
    #TODO: DOC!
    return ('Missing variable "%s" in %s, make sure that you have '
            'not made any errors to your configurations and to start over '
            'copy the template file %s to %s in your '
            'installation directory and edit it to suit your environment'
            ) % (var, CONF_FNAME, CONF_TEMPLATE_FNAME, CONF_FNAME)

def _miss_config_msg():
    #TODO: DOC!
    return ('Missing file %s in the installation dir. If this is a new '
            'installation, copy the template file %s to %s in '
            'your installation directory ("cp %s %s") and edit '
            'it to suit your environment.'
            ) % (CONF_FNAME, CONF_TEMPLATE_FNAME, CONF_FNAME, CONF_FNAME, CONF_TEMPLATE_FNAME)

# TODO: Split up main or somehow make it more read-able
def main(args):
    # Check the Python version, if it is incompatible print a manually crafted
    # json error. This needs to be updated manually as the protocol changes.
    if (version_info[0] != REQUIRED_PY_VERSION_MAJOR or 
        version_info[1] < REQUIRED_PY_VERSION_MINOR):
        print 'Content-Type: application/json\n'
        print INVALID_PY_JSON
        return -1

    # NOTE: It is essential to parse the request before anything else, if
    #       we fail with any kind of error this ensures us that the client
    #       will get the response back.
    # TODO: wrap this in try (not protected now)
    params = FieldStorage()
    
    # From now on we know we have access to dumps
    from jsonwrap import dumps

    # And messaging
    from message import Messager
   
    # Do configuration checking and importing
    from sys import path
    from copy import deepcopy
    from os.path import dirname
    # Reset the path to force config.py to be in this dir (could be hacked
    #       using __init__.py, but we can be monkey-patched anyway)
    orig_path = deepcopy(path)
    # Can't you empty in O(1) instead of O(N)?
    while path:
        path.pop()
    path.append(dirname(__file__))
    # Check if we have a config, otherwise whine
    try:
        import config
        del config
    except ImportError:
        path.extend(orig_path)
        print 'Content-Type: application/json\n'
        Messager.error(_miss_config_msg(), duration=-1)
        print dumps(Messager.output_json({}))
        raise
    # Try importing the config entries we need
    try:
        from config import DEBUG
    except ImportError:
        path.extend(orig_path)
        print 'Content-Type: application/json\n'
        Messager.error(_miss_var_msg('DEBUG'), duration=-1)
        print dumps(Messager.output_json({}))
        raise
    try:
        from config import ADMIN_CONTACT_EMAIL
    except ImportError:
        path.extend(orig_path)
        print 'Content-Type: application/json\n'
        Messager.error(_miss_var_msg('ADMIN_CONTACT_EMAIL'), duration=-1)
        print dumps(Messager.output_json({}))
        raise
    # Remove our entry to the path
    path.pop()
    # Then restore it
    path.extend(orig_path)

    try:
        try:
            # Enable logging
            from logging import basicConfig as log_basic_config
            from config import WORK_DIR
            try:
                from config import LOG_LEVEL
            except ImportError:
                from logging import WARNING as LOG_LEVEL_WARNING
                LOG_LEVEL = LOG_LEVEL_WARNING
            log_basic_config(filename=path_join(WORK_DIR, 'server.log'),
                    level=LOG_LEVEL)

            # Dispatch the request and get the corresponding json dictionary
            from dispatch import dispatch
            client_ip = environ['REMOTE_ADDR']
            try:
                client_hostname = environ['REMOTE_HOST']
            except KeyError:
                client_hostname = None

            json_dic = dispatch(params, client_ip, client_hostname)
            # TODO: Possibly a check here that what we got back was dictionary:ish

            get_session().print_cookie()
        
            # TODO: Generically catch all responses in order to log them
            server_response = ('Content-Type: application/json\n\n' +
                    dumps(Messager.output_json(json_dic)))
            log_info('Server Response:\n' + server_response)
            print  server_response
        except ProtocolError, e:
            # Internal exception, notify the client but don't log to stderr
            json_dic = {}
            e.json(json_dic)
            # Add human-readable version of the error
            Messager.error(str(e))
            server_response = ('Content-Type: application/json\n\n' +
                    dumps(Messager.output_json(json_dic)))
            log_info('Server Response:\n' + server_response)
            print  server_response
        except NoPrintJSONError:
            pass # We are simply to exit and do nothing
    except BaseException, e:
        # Catches even an interpreter crash and syntax error
        if DEBUG:
            # Send back the stack-trace as json
            from traceback import print_exc
            try:
                from cStringIO import StringIO
            except ImportError:
                from StringIO import StringIO

            # Getting the stack-trace requires a small trick
            buf = StringIO()
            print_exc(file=buf)
            buf.seek(0)
            error_msg = '\n'.join(('Server Python crash, stacktrace is:\n', buf.read()))
            Messager.error(error_msg, duration=-1)
        else:
            # Give the user an error message
            from time import time
            # Use the current time since epoch as an id for later log look-up
            error_msg = ('The server encountered a serious error, '
                    'please contact the administrators at %s '
                    'and give the id #%d'
                    ) % (ADMIN_CONTACT_EMAIL, int(time()))
            Messager.error(error_msg, duration=-1)

        # Allow the exception to fall through so it is logged by Apache
        print 'Content-Type: application/json\n'
        json_dic = {
                'exception': True,
                }
        print dumps(Messager.output_json(json_dic))
        raise
    return 0

if __name__ == '__main__':
    from sys import argv, exit
    exit(main(argv))
