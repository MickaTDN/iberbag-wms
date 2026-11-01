"""Bound POST bodies before routing/dependencies can open a database transaction."""
import asyncio
from starlette.responses import JSONResponse

MAX_POST_BYTES = 65536  # Existing operational forms; admin forms retain 8192.
POST_BODY_TIMEOUT = 10.0


class BoundedPostBody:
    def __init__(self, app, *, timeout=POST_BODY_TIMEOUT, limit=MAX_POST_BYTES):
        self.app, self.timeout, self.limit = app, timeout, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        lengths = [v for k, v in scope['headers'] if k.lower() == b'content-length']
        async def refuse(status, detail):
            # Do not reuse an HTTP/1 connection with an unread/incomplete body.
            headers = {'Connection':'close'} if scope.get('http_version','1.1').startswith('1.') else {}
            await JSONResponse({'detail':detail},status,headers=headers)(scope,receive,send)
        try:
            if len(lengths) > 1 or (lengths and (len(lengths[0]) > 12 or not lengths[0].isdigit())):
                return await refuse(400,'Comprimento do formulário inválido.')
            if lengths and int(lengths[0]) > self.limit:
                return await refuse(413,'Formulário demasiado grande.')
            body = bytearray()
            async with asyncio.timeout(self.timeout):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return  # No downstream session/lock has been created.
                    chunk = message.get('body', b'')
                    if len(body) + len(chunk) > self.limit:
                        return await refuse(413,'Formulário demasiado grande.')
                    body.extend(chunk)
                    if not message.get('more_body', False):
                        break
                if lengths and len(body) != int(lengths[0]):
                    return await refuse(400,'Comprimento do formulário inválido.')
        except TimeoutError:
            return await refuse(408,'Tempo limite de receção do formulário excedido.')
        scope.setdefault('state', {})['wms_body'] = bytes(body)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
