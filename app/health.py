"""Small HTTP liveness endpoint; no wallet addresses or credentials exposed."""
import asyncio
import json
import os
import time

async def start_health_server(monitor):
    started = time.time()
    async def handle(reader, writer):
        try:
            await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 3)
            snapshot = await monitor.health_snapshot()
            payload = json.dumps(snapshot).encode()
            status = '200 OK' if snapshot['status'] == 'ok' else '503 Service Unavailable'
            writer.write(f'HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n'.encode()+payload)
            await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
    return await asyncio.start_server(handle, '0.0.0.0', int(os.getenv('PORT','8080')))
