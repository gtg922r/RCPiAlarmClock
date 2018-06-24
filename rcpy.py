import logging
import queue
import logging.handlers


def setupQueueLogger(loggerName, loggingLevel=None):
    '''(rpc function) Setup queue-based logger that logs to queue, and listener the
a streams from queue'''
    que = queue.Queue(-1)  # no limit on size

    log = logging.getLogger(loggerName)
    queue_handler = logging.handlers.QueueHandler(que)
    log.addHandler(queue_handler)

    stream_handler = logging.StreamHandler()
    listener = logging.handlers.QueueListener(que, stream_handler)
    formatter = logging.Formatter(
        '%(asctime)s %(name)s | %(levelname)8s: %(message)s')
    stream_handler.setFormatter(formatter)
    listener.start()

    if(loggingLevel):
        log.setLevel(loggingLevel)

    return log


