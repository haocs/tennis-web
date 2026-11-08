"""Bounded metadata-only client for the separately deployed processing server."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class ProcessingUnavailable(Exception):pass
class ProcessingRejected(Exception):
    def __init__(self,status,body):self.status,self.body=status,body


class ProcessingClient:
    def __init__(self,url):
        self.url=url.rstrip('/') if url else None
        if self.url:
            parsed=urlsplit(self.url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Processing API URL must be a credential-free HTTP(S) URL')
    def call(self,path,body=None,method=None,key=None):
        if not self.url:raise ProcessingUnavailable('Processing server is not configured')
        headers={'Content-Type':'application/json','X-Tennis-Request':'1'}
        if key:headers['Idempotency-Key']=key
        request=urllib.request.Request(self.url+'/api/v1'+path,data=None if body is None else json.dumps(body,separators=(',',':'),allow_nan=False).encode(),headers=headers,method=method)
        try:
            with urllib.request.urlopen(request,timeout=8) as response:
                raw=response.read(512*1024+1)
                if len(raw)>512*1024:raise ProcessingUnavailable('Processing response is too large')
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            try:body=json.loads(error.read(8192))
            except (ValueError,OSError):body={'error':'Processing server rejected the request'}
            raise ProcessingRejected(error.code,body) from error
        except (OSError,ValueError) as error:raise ProcessingUnavailable('Processing server is unavailable; retry this request when it returns') from error
