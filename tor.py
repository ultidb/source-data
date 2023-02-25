import stem.process, re
import logging as log
from os.path import normpath
import psutil

def get_process_by_name_port(process_name, port):
    processes = [proc for proc in psutil.process_iter() if proc.name()
                 == process_name]
    for p in processes:
        for c in p.connections():
            if c.status == 'LISTEN' and c.laddr.port == port:
                return p
    return None

def startTorServer():
    log.info("Starting tor server")
    SOCKS_PORT = 9050
    TOR_PATH = normpath('tor')

    tor_process = stem.process.launch_tor_with_config(
        config={
            'SocksPort': str(SOCKS_PORT),
            'ControlPort': '9051',
        },
        init_msg_handler=lambda line: print(line) if
        re.search('Bootstrapped', line) else False,
        tor_cmd=TOR_PATH
    )

    return tor_process

def killTor(process):
    log.info("Killing tor process")
    process.kill()

def torIsRunning():
    process_python_9050 = get_process_by_name_port('tor', 9050)
    if process_python_9050:
        return True
    else:
        return False


