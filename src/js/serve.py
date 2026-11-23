"""Serve the local workbench and run its cases through main.py."""
import argparse
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
import json
import mimetypes
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import socket
import subprocess
import sys
from threading import Lock, Thread
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4
import webbrowser


WEB = Path(__file__).resolve().parent
PROJECT = WEB.parents[1]
CASES = PROJECT / 'cases'
RUNS = CASES / 'web-runs'
STATIC_FILES = {
    '/': WEB / 'index.html',
    '/index.html': WEB / 'index.html',
    '/styles.css': WEB / 'styles.css',
    '/app.mjs': WEB / 'app.mjs',
    '/case-model.mjs': WEB / 'case-model.mjs',
    '/logo.png': WEB.parent / '深蓝船艏与橙色弧线标志.png',
}
DRAWING_ROLES = ('lines', 'general_arrangement', 'typical_section')
RUN_ID = re.compile(r'[0-9a-f]{32}')
RUN_STATE = 'run-state.json'
RUNTIME_PACKAGES = ('numpy', 'scipy', 'shapely', 'ezdxf', 'fonttools')
MAX_REQUEST_BYTES = 8 * 1024 * 1024
CONTENT_TYPES = {
    '.mjs': 'text/javascript; charset=utf-8',
    '.html': 'text/html; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8',
}


def read_json(path):
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('算例配置必须是 JSON 对象。')
    return config


def templates():
    return {
        path.parent.name: path
        for path in sorted(CASES.glob('*/case.json'))
        if path.parent != RUNS and path.resolve().is_relative_to(CASES.resolve())
    }


def local_input(base, value, suffix, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{label}：请输入文件路径。')
    value = value.replace('\\', '/')
    path = Path(value)
    windows_path = PureWindowsPath(value)
    if value.startswith('//') or (windows_path.anchor and not path.is_absolute()):
        raise ValueError(f'{label}：请输入本机的绝对路径或相对于算例的路径。')
    path = (base / path).resolve()
    if path.as_posix().startswith('//') or path.suffix.lower() != suffix or not path.is_file():
        raise ValueError(f'{label}：找不到 {suffix} 文件 {path}')
    return str(path)


def prepare_config(config, base):
    if not isinstance(config, dict):
        raise ValueError('算例配置必须是 JSON 对象。')
    mother = config.get('mother_ship')
    paths = mother.get('drawings') if isinstance(mother, dict) else None
    if not isinstance(paths, dict) or set(paths) != set(DRAWING_ROLES):
        raise ValueError('母型图纸必须包含型线图、总布置图和典型横剖面图。')
    mother['drawings'] = {
        role: local_input(base, paths[role], '.dxf', f'mother_ship.drawings.{role}')
        for role in DRAWING_ROLES
    }
    if isinstance(config.get('loading'), str):
        config['loading'] = local_input(base, config['loading'], '.json', 'loading')

    revision = drawing_revision(config)
    config['results_dir'] = 'results'
    config['cache_dir'] = 'cache'
    return revision


def drawing_revision(config):
    drawing_settings = config.get('drawings', {})
    if not isinstance(drawing_settings, dict):
        raise ValueError('出图设置必须是 JSON 对象。')
    revision = drawing_settings.get('revision', '')
    if not isinstance(revision, str):
        raise ValueError('图纸修订目录必须是相对路径。')
    revision_path = PureWindowsPath(revision)
    if revision_path.anchor or any(
        part.endswith(('.', ' ')) or re.search(r'[<>:"|?*\x00-\x1f]', part)
        for part in revision_path.parts
    ):
        raise ValueError('图纸修订目录必须位于 results/13 下，并使用有效的目录名。')

    return revision_path


def file_checksum(path):
    checksum = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def package_version(package):
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def calculation_key(config, payload):
    source = PROJECT / 'src'
    code = [PROJECT / 'main.py']
    for folder in sorted(source.iterdir()):
        if folder.is_dir() and (folder.name == 'fsgy_surf' or re.match(r'^\d{2}_', folder.name)):
            code.extend(sorted(path for path in folder.rglob('*')
                               if path.suffix in {'.py', '.html', '.json', '.dxf'} and path.is_file()))
    inputs = dict(config['mother_ship']['drawings'])
    if isinstance(config.get('loading'), str):
        inputs['loading'] = config['loading']
    return sha256(json.dumps({
        'config': payload,
        'inputs': {key: file_checksum(path) for key, path in inputs.items()},
        'code': {str(path.relative_to(PROJECT)): file_checksum(path) for path in code},
        'python': [sys.executable, sys.version, os.getenv('PYTHONPATH')],
        'packages': {package: package_version(package) for package in RUNTIME_PACKAGES},
    }, sort_keys=True).encode('utf-8')).hexdigest()


def saved_run_directories():
    if not RUNS.is_dir() or RUNS.resolve() != RUNS:
        return []
    candidates = []
    for directory in RUNS.iterdir():
        config = directory / 'case.json'
        try:
            if RUN_ID.fullmatch(directory.name) and config.resolve() == config and config.is_file():
                candidates.append((config.stat().st_mtime_ns, directory))
        except (OSError, RuntimeError):
            continue
    return [directory for _, directory in sorted(candidates, reverse=True)]


def result_stages(directory):
    return {row['stage'] for row in read_json(directory / 'results' / 'run.json')['stages']}


def intact_results(directory, payload):
    try:
        case = directory / 'case.json'
        if case.resolve() != case or case.read_text(encoding='utf-8') != payload:
            return False
        stages = result_stages(directory)
        if not stages or not stages <= set(range(1, 14)):
            return False
        if set(range(1, 6)) <= stages and not (directory / 'results' / 'report_01_05.html').is_file():
            return False
        for stage in stages:
            manifest = read_json(directory / 'cache' / f'{stage:02d}' / 'stage.json')
            for filename, checksum in manifest['artifacts'].items():
                path = Path(filename).resolve()
                if not path.is_relative_to(directory) or file_checksum(path) != checksum:
                    return False
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
        return False
    return True


def legacy_status(directory, log):
    last = next((line.strip() for line in reversed(log) if line.strip()), '')
    if any(line.startswith(('Calculation failed:', 'Traceback (most recent call last):')) for line in log):
        return 'failed', '计算失败，请查看运行日志。'
    try:
        stages = result_stages(directory)
    except (OSError, ValueError, TypeError, KeyError):
        stages = set()
    if set(range(1, 6)) <= stages:
        if last.startswith('01-05 report: ') and (directory / 'results' / 'report_01_05.html').is_file():
            return 'succeeded', None
    elif stages and last.startswith('Results: '):
        return 'succeeded', None
    return 'interrupted', '服务中已无此运行的进程记录，已生成的文件仍可查看。'


@dataclass
class Run:
    directory: Path
    revision: PureWindowsPath
    name: str | None = None
    input_key: str | None = None
    status: str = 'running'
    error: str | None = None
    log: deque = field(default_factory=lambda: deque(maxlen=500))
    lock: Lock = field(default_factory=Lock)

    @classmethod
    def restore(cls, directory):
        case = directory / 'case.json'
        try:
            if case.resolve() != case:
                return None
            config = read_json(case)
        except (OSError, RuntimeError, ValueError):
            return None
        try:
            revision = drawing_revision(config)
        except ValueError:
            revision = PureWindowsPath()
        state = directory / RUN_STATE
        logfile = directory / 'run.log'
        log = deque(maxlen=500)
        try:
            if logfile.resolve() == logfile and logfile.is_file():
                with logfile.open(encoding='utf-8', errors='replace') as stream:
                    log.extend(stream)
        except (OSError, RuntimeError):
            pass
        try:
            metadata = read_json(state) if state.resolve() == state else {}
        except (OSError, RuntimeError, ValueError):
            metadata = {}
        status = metadata.get('status')
        error = metadata.get('error')
        if status not in ('succeeded', 'failed'):
            status, error = legacy_status(directory, log)
        return cls(directory, revision, name=config.get('name'), input_key=metadata.get('input_key'),
                   status=status, error=error, log=log)

    def snapshot(self):
        with self.lock:
            status = self.status
            error = self.error
            log = ''.join(self.log)

        files = [(Path('case.json'), '本次运行配置')]
        if status != 'running':
            files.extend([
                (Path('results/12/report.html'), '综合计算报告'),
                (Path('results/report_01_05.html'), '01–05 计算报告'),
                (Path('results/13', *self.revision.parts, 'index.html'), '设计图纸与 DXF 下载'),
                (Path('results/02/ship.html'), '三维船体'),
                (Path('results/run.json'), '运行记录'),
            ])
        artifacts = [
            {'label': label, 'url': f'/runs/{self.directory.name}/{quote(path.as_posix())}'}
            for path, label in files if (self.directory / path).is_file()
        ]
        return {'name': self.name or self.directory.name, 'status': status, 'error': error,
                'log': log, 'artifacts': artifacts}

    def calculate(self, force):
        args = [sys.executable, '-u', str(PROJECT / 'main.py'), str(self.directory / 'case.json')]
        if force:
            args.append('--force')
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        error = None
        try:
            with subprocess.Popen(
                args, cwd=PROJECT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace',
            ) as process, (self.directory / 'run.log').open('w', encoding='utf-8') as log_file:
                for line in process.stdout:
                    log_file.write(line)
                    log_file.flush()
                    with self.lock:
                        self.log.append(line)
                if code := process.wait():
                    error = f'计算退出（代码 {code}），请查看运行日志。'
        except OSError as exc:
            error = f'运行 Python 计算失败：{exc}'
        status = 'failed' if error else 'succeeded'
        input_key = None
        if not error:
            try:
                case = self.directory / 'case.json'
                if calculation_key(read_json(case), case.read_text(encoding='utf-8')) == self.input_key:
                    input_key = self.input_key
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                pass
        state = self.directory / RUN_STATE
        temporary = state.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps({'status': status, 'error': error, 'input_key': input_key},
                                            ensure_ascii=False), encoding='utf-8')
            temporary.replace(state)
        except OSError:
            with self.lock:
                self.log.append('运行状态未能保存到磁盘。\n')
        with self.lock:
            self.error = error
            self.input_key = input_key
            self.status = status


class Workbench(ThreadingHTTPServer):
    def __init__(self, port):
        super().__init__(('127.0.0.1', port), Handler)
        port = self.server_port
        suffix = '' if port == 80 else f':{port}'
        self.local_hosts = {f'127.0.0.1{suffix}', f'localhost{suffix}'}
        self.runs = {}
        self.runs_lock = Lock()

    def matching_run(self, input_key, payload):
        for run in reversed(self.runs.values()):
            with run.lock:
                matches = run.input_key == input_key
                status = run.status
            if matches and (status == 'running' or (status == 'succeeded' and intact_results(run.directory, payload))):
                return run.directory.name, status == 'succeeded'
        for directory in saved_run_directories():
            if directory.name in self.runs:
                continue
            try:
                state = directory / RUN_STATE
                metadata = read_json(state) if state.resolve() == state else {}
            except (OSError, ValueError, RuntimeError):
                continue
            if metadata.get('input_key') == input_key and metadata.get('status') == 'succeeded':
                if intact_results(directory, payload):
                    return directory.name, True
        return None

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not self.check_origin():
            return
        path = unquote(urlsplit(self.path).path)
        if '\x00' in path:
            return self.send_json(400, {'error': '请求路径无效。'})
        if path in STATIC_FILES:
            return self.send_file(STATIC_FILES[path])
        if path == '/api/templates':
            available = []
            for ident, file in templates().items():
                try:
                    name = read_json(file).get('name', ident)
                except (OSError, ValueError):
                    name = ident
                available.append({'id': ident, 'name': name if isinstance(name, str) else ident})
            return self.send_json(200, {'templates': available})
        if path.startswith('/api/templates/'):
            ident = path.removeprefix('/api/templates/')
            if file := templates().get(ident):
                try:
                    config = read_json(file)
                except (OSError, ValueError) as exc:
                    return self.send_json(400, {'error': f'无法读取算例模板：{exc}'})
                return self.send_json(200, {'id': ident, 'config': config})
        if path == '/api/runs/latest':
            with self.server.runs_lock:
                directories = saved_run_directories()
            return self.send_json(200, {'id': directories[0].name if directories else None})
        if path.startswith('/api/runs/'):
            ident = path.removeprefix('/api/runs/')
            run = self.server.runs.get(ident)
            if run is None and RUN_ID.fullmatch(ident) and RUNS.resolve() == RUNS:
                run = Run.restore(RUNS / ident)
            if run:
                return self.send_json(200, run.snapshot())
        if path.startswith('/runs/'):
            return self.send_artifact(path)
        self.send_json(404, {'error': '找不到所请求的页面或算例。'})

    def do_POST(self):
        if not self.check_origin():
            return
        if urlsplit(self.path).path != '/api/runs':
            return self.send_json(404, {'error': '找不到所请求的接口。'})
        if self.headers.get_content_type() != 'application/json':
            return self.send_json(415, {'error': '请以 JSON 提交算例配置。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_REQUEST_BYTES:
                return self.send_json(413, {'error': '算例配置为空或超出 8 MB。'})
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict):
                raise ValueError('请求必须是 JSON 对象。')
            base_id = request.get('baseId', 'example')
            force = request.get('force', False)
            if not isinstance(base_id, str) or not isinstance(force, bool):
                raise ValueError('算例模板或运行选项无效。')
            template = templates().get(base_id)
            if template is None:
                raise ValueError('找不到所选算例模板。')
            config = request.get('config')
            revision = prepare_config(config, template.parent)
            payload = json.dumps(config, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
            if RUNS.resolve() != RUNS:
                raise ValueError('运行目录须位于项目的 cases/web-runs 下。')
            input_key = calculation_key(config, payload)
            with self.server.runs_lock:
                matching = None if force else self.server.matching_run(input_key, payload)
                if not matching:
                    ident = uuid4().hex
                    directory = RUNS / ident
                    directory.mkdir(parents=True)
                    (directory / 'case.json').write_text(payload, encoding='utf-8')
                    run = Run(directory, revision, name=config.get('name'), input_key=input_key)
                    self.server.runs[ident] = run
        except (ValueError, OSError, RuntimeError) as exc:
            return self.send_json(400, {'error': str(exc)})

        if matching:
            ident, reused = matching
            return self.send_json(202, {'id': ident, 'reused': reused})
        Thread(target=run.calculate, args=(force,), daemon=True).start()
        self.send_json(202, {'id': ident})

    def check_origin(self):
        host = self.headers.get('Host', '').lower()
        origin = self.headers.get('Origin')
        cross_site_write = self.command == 'POST' and self.headers.get('Sec-Fetch-Site') == 'cross-site'
        if (host in self.server.local_hosts and
                (origin is None or origin == f'http://{host}') and not cross_site_write):
            return True
        self.send_json(403, {'error': '仅接受本机工作台发起的请求。'})
        return False

    def send_artifact(self, path):
        ident, _, relative = path.removeprefix('/runs/').partition('/')
        if RUN_ID.fullmatch(ident):
            directory = RUNS / ident
            if relative == 'case.json':
                file = directory / relative
                if file.resolve() == file:
                    return self.send_file(file)
            if relative.startswith('results/'):
                root = directory / 'results'
                file = (directory / relative).resolve()
                if root.resolve() == root and file.is_relative_to(root):
                    return self.send_file(file)
        self.send_json(404, {'error': '找不到所请求的运行文件。'})

    def send_headers(self, status, kind, length):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        if kind.startswith('text/html'):
            self.send_header('Content-Security-Policy', "frame-ancestors 'self'; base-uri 'self'")
        self.end_headers()

    def send_json(self, status, value):
        payload = json.dumps(value, ensure_ascii=False).encode('utf-8')
        self.send_headers(status, 'application/json; charset=utf-8', len(payload))
        self.wfile.write(payload)

    def send_file(self, path):
        if not path.is_file():
            return self.send_json(404, {'error': '找不到所请求的文件。'})
        with path.open('rb') as source:
            mime = CONTENT_TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0]
            self.send_headers(200, mime or 'application/octet-stream', os.fstat(source.fileno()).st_size)
            try:
                shutil.copyfileobj(source, self.wfile)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_request(self, code='-', size='-'):
        if str(code) != '200':
            super().log_request(code, size)


def open_browser(url):
    try:
        if webbrowser.open(url, new=2):
            return
    except (OSError, webbrowser.Error):
        pass
    print(f'未能自动打开浏览器，请手动访问 {url}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='启动 Fsgy-surf 本地工作台。')
    parser.add_argument('--port', type=int, default=8765, help='本地 HTTP 端口（默认 8765）')
    parser.add_argument('--no-browser', action='store_true', help='只启动本地服务')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('--port 必须在 0 到 65535 之间。')
    try:
        server = Workbench(args.port)
    except OSError as exc:
        parser.exit(1, f'启动工作台失败：{exc}\n')
    with server:
        url = f'http://127.0.0.1:{server.server_port}/'
        print(f'Fsgy-surf {url}', flush=True)
        if not args.no_browser:
            Thread(target=open_browser, args=(url,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
