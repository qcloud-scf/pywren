import base64
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback
from threading import Thread
import platform
from uuid import getnode as get_mac


if sys.version_info > (3, 0):
    from queue import Queue, Empty
    #from . import version
else:
    from Queue import Queue, Empty
#    import version

TEMP = "D:\local\Temp"
PYTHON_MODULE_PATH = os.path.join(TEMP, "modules")

logger = logging.getLogger(__name__)
PROCESS_STDOUT_SLEEP_SECS = 2

def download_runtime_if_necessary(azclient, runtime_bucket, runtime_key):
    """
    Download the runtime if necessary
    return True if cached, False if not (download occured)
    """
    # figure this out later.
    return True

def b64str_to_bytes(str_data):
    str_ascii = str_data.encode('ascii')
    byte_data = base64.b64decode(str_ascii)
    return byte_data

def az_handler(event, context):
    logger.setLevel(logging.INFO)
    return generic_handler(event, context)

def get_server_info():
    server_info = {'uname': ' '.join(platform.uname()),
                    'mac': get_mac()}
    return server_info

def generic_handler(event, context_dict):
    """
    context_dict is generic infromation about the context
    that we are running in, provided by the scheduler
    """
    response_status = {'exception': None}
    try:
        if event['storage_config']['storage_backend'] != 'az':
            raise NotImplementedError(("Using {} as storage backend is not supported " +
                                       "yet.").format(event['storage_config']['storage_backend']))
        bucket = event['storage_config']['backend_config']['container']

        logger.info("invocation started")

        # download the input

#        if version.__version__ != event['pywren_version']:
#            raise Exception("WRONGVERSION", "Pywren version mismatch",
#                            version.__version__, event['pywren_version'])

        start_time = time.time()
        response_status['start_time'] = start_time
        print "lol"
        func_filename = os.environ["funcfile"]
        data_filename = os.environ["datafile"]
        output_filename = os.environ["outputfile"]
        data_byte_range = event["data_byte_range"]
        job_max_runtime=event.get("job_max_runtime", 300)
        # download times don't make sense on azure since everything's preloaded.
        start, end = None, None
        if data_byte_range is None:
            pass

        # The byte range is the entire file, which is already loaded.
        elif data_byte_range[0] == 0 \
                 and data_byte_range[1] + 1 == os.path.getsize(data_filename):
            print "hello"
            pass

        else:
            print "hi"
            start, end = data_byte_range
            #data_write_fid = open(data_filename, 'wb')
            #data_read_fid = open(data_filename, "rb")
            #data_read_fid.seek(data_byte_range[0])
            #read_data = data_read_fid.read(data_byte_range[1] + 1)
            #data_write_fid.write(read_data)
            #data_write_fid.close()
            #data_read_fid.close()

        # now split
        d = json.load(open(func_filename, 'r'))
        #shutil.rmtree(PYTHON_MODULE_PATH, True) # delete old modules
        #os.mkdir(PYTHON_MODULE_PATH)
        # get modules and save
        for m_filename, m_data in d['module_data'].items():
            m_path = os.path.dirname(m_filename)
            if len(m_path) > 0 and m_path[0] == "\\":
                m_path = m_path[1:]
            #fix windows forward slash delimeter
            m_path = os.path.join(*filter(lambda x: len(x) > 0, m_path.split("/")))
            to_make = os.path.join(PYTHON_MODULE_PATH, m_path)

            #print "to_make=", to_make, "m_path=", m_path
            try:
                os.makedirs(to_make)
            except OSError as e:
                if e.errno == 17:
                    pass
                else:
                    raise e
            full_filename = os.path.join(to_make, os.path.basename(m_filename))
            fid = open(full_filename, 'wb')
            fid.write(b64str_to_bytes(m_data))
            fid.close()

        logger.info("Finished writing {} module files".format(len(d['module_data'])))
        #logger.info("Runtime ready, cached={}".format(runtime_cached))
        #response_status['runtime_cached'] = runtime_cached

        cwd = os.getcwd()
        jobrunner_path = "D:\\home\\site\\wwwroot\\jobrunner\\run.py"

        extra_env = event.get('extra_env', {})
        extra_env['PYTHONPATH'] = "{};{}".format(os.getcwd(), PYTHON_MODULE_PATH)

        call_id = event['call_id']
        callset_id = event['callset_id']
        response_status['call_id'] = call_id
        response_status['callset_id'] = callset_id

        CONDA_PYTHON_PATH = "D:\home\site\wwwroot\conda\Miniconda2"
        CONDA_PYTHON = os.path.join(CONDA_PYTHON_PATH ,"python")
        CONDA_PYTHON_RUNTIME = os.path.join(CONDA_PYTHON_PATH, "python")


        cmdstr = "{} {} {} {} {} {} {}".format(CONDA_PYTHON_RUNTIME,
                                         jobrunner_path,
                                         func_filename,
                                         data_filename,
                                         output_filename,
                                         start,
                                         end)

        setup_time = time.time()
        response_status['setup_time'] = setup_time - start_time

        local_env = os.environ.copy()

        local_env["OMP_NUM_THREADS"] = "1"
        local_env.update(extra_env)

        local_env['PATH'] = "{};{}".format(CONDA_PYTHON_PATH, local_env.get("PATH", ""))

        logger.debug("command str=%s", cmdstr)
        # This is copied from http://stackoverflow.com/a/17698359/4577954
        # reasons for setting process group: http://stackoverflow.com/a/4791612
        process = subprocess.Popen(cmdstr, shell=True, env=local_env, bufsize=1,
                                   stdout=subprocess.PIPE)
        process.communicate()
        logger.info("launched process")
        def consume_stdout(stdout, queue):
            with stdout:
                for line in iter(stdout.readline, b''):
                    queue.put(line)

        q = Queue()

        t = Thread(target=consume_stdout, args=(process.stdout, q))
        t.daemon = True
        t.start()

        stdout = b""
        while t.isAlive():
            try:
                line = q.get_nowait()
                stdout += line
                logger.info(line)
            except Empty:
                time.sleep(PROCESS_STDOUT_SLEEP_SECS)
            total_runtime = time.time() - start_time
            # how to do this in windows?
            if total_runtime > job_max_runtime:
                logger.warn("Process exceeded maximum runtime of {} sec".format(job_max_runtime))
                # Send the signal to all the process groups
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                raise Exception("OUTATIME",
                                "Process executed for too long and was killed")

        logger.info("command execution finished")

        end_time = time.time()

        response_status['stdout'] = stdout.decode("ascii")
        response_status['exec_time'] = time.time() - setup_time
        response_status['end_time'] = end_time
        response_status['host_submit_time'] = event['host_submit_time']

        response_status.update(context_dict)
    except Exception as e:
        # internal runtime exceptions
        response_status['exception'] = str(e)
        response_status['exception_args'] = e.args
        response_status['exception_traceback'] = traceback.format_exc()
    finally:
        response_status['server_info'] = get_server_info()

        status_file = open(os.environ["statusfile"], 'w')
        status_file.write(json.dumps(response_status))
        status_file.close()

if __name__ == "__main__":
    event_file = open(os.environ["queueMessage"]).read()
    az_handler(json.loads(event_file), {})