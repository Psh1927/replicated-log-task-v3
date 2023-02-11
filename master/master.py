from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import requests
import logging
from threading import Thread
from threading import Condition
import time


class CountDownLatch:
    def __init__(self, count, count_good_result):
        self.count = count
        self.count_good_result = count_good_result
        self.condition = Condition()

    def count_down(self, result):
        with self.condition:
            if self.count == 0:
                return
            self.count -= 1
            if result:
                self.count_good_result -= 1
            if self.count == 0:
                self.condition.notify_all()
            if self.count_good_result == 0:
                self.condition.notify_all()

    def wait(self):
        with self.condition:
            if self.count == 0 or self.count_good_result == 0:
                return
            self.condition.wait()


secondaries = [{'name': 'secondary-1', 'address': 'http://127.0.0.1:8081', 'status': 'suspected'},
                {'name': 'secondary-2', 'address': 'http://127.0.0.1:8082', 'status': 'suspected'}]
memory_list = list()
quorum = False
id_count = 1
FORMAT = '%(asctime)s %(message)s'
logging.basicConfig(format=FORMAT, level=0)


def heartbeat_sender():
    global quorum
    while True:
        for secondary in secondaries:
            time.sleep(10)
            try:
                if requests.get(secondary['address'] + '/health', timeout=3).ok:
                    logging.info("Get heartbeat from " + secondary['name'] + " : True")
                    if secondary['status'] == 'unhealthy':
                        secondary['status'] = 'suspected'
                    elif secondary['status'] == 'suspected':
                        secondary['status'] = 'healthy'
                else:
                    if secondary['status'] == 'healthy':
                        secondary['status'] = 'suspected'
                    elif secondary['status'] == 'suspected':
                        secondary['status'] = 'unhealthy'
                    logging.error("Get heartbeat from " + secondary['name'] + " : False")
            except:
                if secondary['status'] == 'healthy':
                    secondary['status'] = 'suspected'
                elif secondary['status'] == 'suspected':
                    secondary['status'] = 'unhealthy'
                logging.error("Get heartbeat from " + secondary['name'] + " : False")
                pass
        healthy_sec = 0
        for secondary in secondaries:
            if secondary['status'] == 'healthy':
                healthy_sec += 1
            logging.info("Status " + secondary['name'] + ": " + secondary['status'])
        if healthy_sec + 1 > (len(secondaries) / 2):
            quorum = True
        else:
            quorum = False
        logging.info("Quorum: " + str(quorum))


def send_to_secondary(latch, secondary, value):
    logging.info('Replication to ' + secondary['name'] + ' ' + secondary['address'] + ': '
                 + 'id=' + str(value['id']) + ' msg=' + value['msg'])
    try:
        result = requests.post(secondary['address'], json=json.dumps(value)).ok
    except:
        result = False
    retry_num = 1
    while not result and secondary['status'] != 'unhealthy':
        logging.info('Retry #' + str(retry_num) + ' replication to ' + secondary['name'] + ' ' + secondary['address']
                     + ': ' + 'id=' + str(value['id']) + ' msg=' + value['msg'])
        try:
            result = requests.post(secondary['address'], json=json.dumps(value)).ok
        except:
            result = False
        retry_num += 1
    latch.count_down(result)
    if result:
        logging.info('Replication to ' + secondary['name'] + ' completed')
    else:
        logging.error('Error with replication to ' + secondary['name'])
    return result


def message_handler(data):
    global id_count
    new_value = {'id': id_count, 'msg': data['value']}
    memory_list.append(new_value)
    logging.info('Write in memory: id=' + str(id_count) + ' msg=' + data['value'])
    id_count += 1

    latch = CountDownLatch(len(secondaries), int(data['w']) - 1)
    for secondary in secondaries:
        thread = Thread(target=send_to_secondary, args=(latch, secondary, new_value))
        thread.start()
    latch.wait()

    if latch.count_good_result > 0:
        return False
    return True


class RequestHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        if len(memory_list) == 0:
            self.wfile.write("Empty list".encode())
        response = ""
        for row in memory_list:
            response +=  str(row['id']) + ' ' + row['msg'] + '\n'
        self.wfile.write(response.encode())

    def do_POST(self):
        if quorum:
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length)
            data = json.loads(json.loads(body))
            if message_handler(data):
                self.send_response(200)
            else:
                self.send_response(408)
            self.send_response(200)
            self.end_headers()
        else:
            logging.error('No quorum. Read-only')
            self.send_response(408)


def main():
    thread = Thread(target=heartbeat_sender)
    thread.start()
    port = 8080
    print('Listening on localhost:%s' % port)
    server = HTTPServer(('', port), RequestHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()