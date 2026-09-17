// Float32 PCM in memory only. Output stays silent; no recording container is made.
class ClinicPCM extends AudioWorkletProcessor {
 constructor(){super();this.buffer=new Float32Array(3200);this.used=0;this.stopped=false;this.port.onmessage=()=>{this.stopped=true;this.flush();this.port.postMessage({flushed:true});};}
 flush(){if(this.used){const samples=this.buffer.slice(0,this.used);this.port.postMessage({pcm:samples.buffer},[samples.buffer]);this.used=0;}}
 process(inputs){if(this.stopped)return false;const channels=inputs[0];if(channels?.length){for(let i=0;i<channels[0].length;i++){let value=0;for(const c of channels)value+=c[i];this.buffer[this.used++]=value/channels.length;if(this.used===this.buffer.length)this.flush();}}return true;}
}
registerProcessor('clinic-pcm',ClinicPCM);
