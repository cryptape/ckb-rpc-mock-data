import json
import urllib.request
import os
from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder='templates')

current_dir = os.path.dirname(__file__)
parent_dir = os.path.dirname(current_dir)
file_storage_root = os.path.join(parent_dir, 'mock')

POST_REQUEST_NOT_EQ_ERROR = 501
READ_FILE_NOT_EXIST_ERROR = 502
DIR_NOT_EXIST_ERROR = 503
PROXY_ERROR = 504  # 代理相关错误码

# 默认 RPC_URL（如果未提供参数时使用）
DEFAULT_RPC_URL = "http://localhost:8227"

@app.route('/')
def index():
    # 返回文件列表页面
    root_files = []
    for file_name in os.listdir(file_storage_root):
        file_path = os.path.join(file_storage_root, file_name)
        if os.path.isdir(file_path):
            sub_files = [sub_file for sub_file in os.listdir(file_path) if
                         not os.path.isfile(os.path.join(file_path, sub_file))]
            root_files.append({"directory": file_name, "files": sub_files})
    return render_template('index.html', files=root_files)

@app.route('/test/<directory>/<filename>', methods=['GET', 'POST'])
def test_get(directory, filename):
    # 下载文件内容作为JSON数据返回
    file_path = os.path.join(file_storage_root, directory, filename)
    print(filename)
    if os.path.exists(file_path):
        try:
            with open(f"{file_path}/request.json", "r") as file:
                request_data = file.read()
            with open(f"{file_path}/response.json", "r") as file:
                response_data = file.read()
            request_data = json.loads(request_data)
            response_data = json.loads(response_data)
            if request.method == 'GET':
                return jsonify({"filename": filename, "request": request_data, "response": response_data})
            if request.method == 'POST':
                # 比较POST请求数据和request.json是否一致
                post_data = request.json
                if "params" not in post_data:
                    post_data["params"] = []  # 将 "params" 字段设为一个空列表，即empty场景
                if post_data['params'] is None:
                    post_data['params'] = request_data['params']  # 将 params 为 None 的情况转换为[]/null

                if post_data['method'] == request_data['method'] and post_data['params'] == request_data['params']:
                    response_data['id'] = post_data['id']
                    return jsonify(response_data)
                else:
                    app.logger.error(
                        f"Request data does not match with the data:\n 'expected request':{request_data}\n sdk post':{post_data}")
                    return jsonify({'id': post_data['id'], 'jsonrpc': '2.0',
                                    "error": f"Request data does not match with the expected data:'request':{request_data},'post':{post_data}"}), POST_REQUEST_NOT_EQ_ERROR
        except Exception as e:
            return jsonify({"error": f"Error while reading the file: {str(e)}"}), READ_FILE_NOT_EXIST_ERROR
    else:
        return jsonify({"error": "File not found"}), DIR_NOT_EXIST_ERROR

@app.route('/proxy', methods=['GET', 'POST', 'OPTIONS'])
def proxy():
    # 从查询参数中获取 RPC_URL
    rpc_url = request.args.get('rpc_url', DEFAULT_RPC_URL)

    if not rpc_url or not rpc_url.startswith(('http://', 'https://')):
        return jsonify({'error': 'Invalid or missing rpc_url parameter'}), 400

    if request.method == 'POST':
        try:
            # 转发 POST 请求到动态 RPC_URL
            rpc_req = urllib.request.Request(
                rpc_url,
                data=request.get_data(),  # 获取请求体（不包含 rpc_url）
                headers={'Content-Type': 'application/json'},
                method='POST'
            )
            with urllib.request.urlopen(rpc_req, timeout=5) as response:
                data = response.read()
                return jsonify(json.loads(data.decode())), 200
        except urllib.error.URLError as e:
            return jsonify({'error': f'Failed to connect to RPC: {str(e)}'}), PROXY_ERROR
        except Exception as e:
            return jsonify({'error': f'Internal proxy error: {str(e)}'}), 500

    elif request.method == 'GET':
        # 返回代理信息，包含当前使用的 RPC_URL
        proxy_info = {
            'proxy_endpoint': '/proxy',
            'rpc_url': rpc_url,
            'supported_methods': ['POST', 'GET', 'OPTIONS'],
            'status': 'active',
            'message': 'This is the proxy server information'
        }
        return jsonify(proxy_info), 200

    elif request.method == 'OPTIONS':
        # 处理 CORS 预检请求
        return jsonify({}), 200, {
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type'
        }

    else:
        return jsonify({'error': 'Method Not Allowed'}), 405

def handler(event, context):
    # 模拟 Flask 请求环境
    class MockRequest:
        def __init__(self, event):
            self.method = event['httpMethod']
            self.headers = event['headers']
            self.data = event.get('body', '').encode() if event.get('body') else None
            self.args = event.get('queryStringParameters', {})
            self.json = json.loads(event.get('body', '{}')) if event.get('body') else {}

    # 创建虚拟请求对象
    mock_request = MockRequest(event)
    with app.test_request_context(path=event['path'], method=mock_request.method, headers=mock_request.headers,
                                  data=mock_request.data, query_string=mock_request.args, json=mock_request.json):
        # 手动分发请求到对应路由
        if mock_request.method in ['GET', 'POST', 'OPTIONS'] and event['path'] == '/proxy':
            return app.full_dispatch_request().get_response().get_data()
        elif mock_request.method in ['GET', 'POST'] and event['path'].startswith('/test/'):
            # 解析 directory 和 filename
            path_parts = event['path'].split('/')[2:]  # 跳过 /test/
            if len(path_parts) == 2:
                directory, filename = path_parts
                with app.test_request_context(path=event['path'], method=mock_request.method,
                                              headers=mock_request.headers, data=mock_request.data,
                                              query_string=mock_request.args, json=mock_request.json):
                    return app.dispatch_request()
            return jsonify({'error': 'Invalid path'}), 400
        elif event['path'] == '/':
            with app.test_request_context(path='/'):
                return app.full_dispatch_request().get_response().get_data()
        else:
            return jsonify({'error': 'Not Found'}), 404

if __name__ == '__main__':
    app.config['JSON_AS_TEXT'] = False
    app.run(debug=True)
