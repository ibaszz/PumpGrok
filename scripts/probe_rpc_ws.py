
import asyncio, json, collections
import websockets
PROGRAM="6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
async def main():
    async with websockets.connect("wss://api.mainnet-beta.solana.com", ping_interval=30, open_timeout=20, max_size=20_000_000) as ws:
        await ws.send(json.dumps({"jsonrpc":"2.0","id":1,"method":"logsSubscribe","params":[{"mentions":[PROGRAM]},{"commitment":"confirmed"}]}))
        kinds=collections.Counter(); n_data=0; sample=None
        end=asyncio.get_event_loop().time()+30
        while asyncio.get_event_loop().time()<end:
            try: raw=await asyncio.wait_for(ws.recv(), timeout=max(1,end-asyncio.get_event_loop().time()))
            except asyncio.TimeoutError: break
            m=json.loads(raw)
            if "id" in m: print("SUBACK", str(m)[:200]); continue
            kinds["notif"]+=1
            logs=(m.get("params",{}).get("result",{}).get("value",{}) or {}).get("logs") or []
            d=[l for l in logs if l.startswith("Program data: ")]
            n_data+=len(d)
            if d and sample is None: sample=d[0][:120]
        print("COUNTS",dict(kinds),"program_data_lines",n_data)
        print("SAMPLE",sample)
asyncio.run(main())
