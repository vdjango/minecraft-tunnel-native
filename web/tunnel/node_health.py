"""Small signed admission hints, deliberately excluding OS traffic counters."""
import math
import time


def validate(value):
    if not isinstance(value,dict):raise ValueError('Invalid routing metadata')
    region=value.get('region','')
    if not isinstance(region,str) or len(region)>80:raise ValueError('Invalid region')
    for key in ('utilization','remaining_visitors'):
        number=value.get(key)
        if number is not None and (type(number) not in (int,float) or not math.isfinite(number) or number<0 or number>(1 if key=='utilization' else 1000000)):
            raise ValueError('Invalid admission hint')
    if type(value.get('accepting',True)) is not bool:raise ValueError('Invalid admission state')
    if value.get('remaining_visitors') is not None and type(value['remaining_visitors']) is not int:raise ValueError('Invalid visitor count')
    return value


def snapshot(federation):
    relay=federation.relay
    if not hasattr(relay,'capacity'):
        return dict(region=getattr(federation,'region',''),accepting=not getattr(relay,'draining',False),
                    utilization=None,remaining_visitors=None)
    key=(getattr(federation,'region',''),relay.draining,relay.max_rooms,len(relay.rooms),len(relay.peers),
         tuple(sorted(relay.capacity.policy.items())))
    cached=getattr(federation,'admission_cache',None)
    if cached is not None and cached[0]==key and time.monotonic()-cached[1]<5:
        return dict(cached[2])
    capacity=relay.capacity.snapshot()
    budget=capacity['bandwidth_mb_s']*(1-capacity['reserve_percent']/100)
    measured=getattr(federation,'bandwidth',None)
    traffic=measured.snapshot() if measured is not None else {}
    outgoing=traffic.get('tx_bytes_per_second') if traffic.get('status')=='ready' else None
    used=max(capacity['payload_mb_s']*1.15,outgoing/1000000 if outgoing is not None else 0)
    result=dict(region=getattr(federation,'region',''),
        accepting=not relay.draining and len(relay.rooms)<relay.max_rooms,
        utilization=round(min(1,used/budget),2) if budget else None,
        remaining_visitors=capacity['remaining_visitors'])
    federation.admission_cache=(key,time.monotonic(),result)
    return dict(result)
