
import asyncio, json, collections
import websockets

async def main():
    cmds = []
    async with websockets.connect("wss://pumpportal.fun/api/data", ping_interval=20, open_timeout=20) as ws:
        await ws.send(json.dumps({"method": "subscribeNewToken"}))
        kinds = collections.Counter()
        samples = {}
        t0 = None
        end = asyncio.get_event_loop().time() + 25
        while asyncio.get_event_loop().time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, end - asyncio.get_event_loop().time()))
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            k = m.get("txType") or m.get("method") or "other"
            kinds[k] += 1
            if k not in samples:
                samples[k] = m
        print("COUNTS", dict(kinds))
        for k, v in samples.items():
            print("SAMPLE", k, json.dumps(v)[:900])

asyncio.run(main())
