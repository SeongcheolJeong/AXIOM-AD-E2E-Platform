"""Bounded host control transport; replay inputs are uploaded once, then paced by Linux."""
import json,os,select,signal,subprocess,sys,time
from pathlib import Path
MAX_MESSAGE=8*1024*1024

class Process:
    def __init__(self,runtime,init,log_path,check=lambda:None,timeout_s=2,command=None,preamble=None):
        self.check=check;self.timeout=timeout_s;self.buffer=b'';self.runtime=runtime
        self.log=Path(log_path).open('wb')
        self.process=subprocess.Popen(command or runtime['command'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=self.log,start_new_session=True)
        os.set_blocking(self.process.stdin.fileno(),False);os.set_blocking(self.process.stdout.fileno(),False)
        try:
            if preamble is not None:
                data=json.dumps(preamble,separators=(',',':')).encode()+b'\n'
                self.process.stdin.write(data);self.process.stdin.flush()
            reply,_=self.exchange(init)
            if reply.get('op')!='init' or reply.get('status')!='ready' or (reply.get('pid')!=self.process.pid if preamble is None else type(reply.get('pid')) is not int or reply['pid']<=0) or reply.get('dut_version')!=runtime['dut_version']:
                raise ValueError('DUT initialization identity differs')
            if reply.get('dut_profile')!=init.get('dut_profile'):raise ValueError('DUT profile acknowledgment differs')
        except BaseException:self.close(False);raise
    def exchange(self,message):
        data=json.dumps(message,allow_nan=False,separators=(',',':')).encode()+b'\n'
        if len(data)>MAX_MESSAGE:raise ValueError('DUT request exceeds protocol bound')
        start=time.perf_counter_ns();deadline=time.monotonic()+self.timeout;offset=0
        while offset<len(data) or b'\n' not in self.buffer:
            self.check()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise TimeoutError('DUT wall-clock response timeout; virtual time did not advance')
            reads,writes,_=select.select([self.process.stdout],[self.process.stdin] if offset<len(data) else [],[],min(.05,remaining))
            if writes:offset+=os.write(self.process.stdin.fileno(),data[offset:])
            if reads:
                raw=os.read(self.process.stdout.fileno(),MAX_MESSAGE+1)
                if not raw:raise RuntimeError('DUT exited without a complete reply')
                self.buffer+=raw
                if len(self.buffer)>MAX_MESSAGE:raise ValueError('DUT reply exceeds protocol bound')
        line,self.buffer=self.buffer.split(b'\n',1)
        reply=json.loads(line,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('Nonfinite DUT JSON')))
        if not isinstance(reply,dict) or reply.get('schema_version')!='hil-dut-reply/1' or reply.get('op')!=message['op']:
            raise ValueError('DUT reply protocol mismatch')
        if message['op']=='step' and reply.get('sequence')!=message['sequence']:raise ValueError('DUT reply sequence differs')
        return reply,(time.perf_counter_ns()-start)/1e6
    def close(self,graceful=True):
        close_error=None;propagating=sys.exc_info()[0] is not None
        try:
            if graceful and self.process.poll() is not None:
                close_error=RuntimeError('DUT exited before close acknowledgment')
            if self.process.poll() is None and graceful:
                try:
                    reply,_=self.exchange({'op':'close'})
                    if reply.get('status')!='closed':raise ValueError('DUT close acknowledgment missing')
                    self.process.wait(timeout=.5)
                except Exception as error:close_error=error
            if self.process.poll() is None:
                try:os.killpg(self.process.pid,signal.SIGTERM)
                except (ProcessLookupError,PermissionError):
                    if self.process.poll() is None:
                        try:self.process.wait(timeout=.1)
                        except subprocess.TimeoutExpired:raise
                try:self.process.wait(timeout=.5)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=2)
        finally:
            for stream in (self.process.stdin,self.process.stdout,self.log):stream.close()
        if close_error is not None and not propagating:raise close_error
