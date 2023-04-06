import os
from flask import Flask, request
import requests, json

app = Flask(__name__)

@app.route('/health')
def healthcheck():
    response = {
        'status': 'OK'
    }
    return json.dumps(response), 200, {'Content-Type': 'application/json'}
    

@app.route('/ingest', methods=['POST'])
def ingest():
    os.system('git pull')

    payload = request.get_json()

    url = 'http://127.0.0.1:3030/v1/ingest'
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json=payload, headers=headers)
    
    return response.content, response.status_code, response.headers.items()


if __name__ == '__main__':
    app.run(debug=True, port=3031)