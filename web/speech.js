/** 麦克风只在这里打开和关闭。输出 16kHz 单声道 16bit PCM，密钥不进浏览器。 */

export function createRecorder() {
  let stream = null;
  let audioContext = null;
  let source = null;
  let analyser = null;
  let processor = null;
  let mute = null;
  let frame = 0;
  let maxLevel = 0;
  let startedAt = 0;
  let frameWriter = null;
  let resampler = null;
  let accepting = false;

  return {
    async start(onSamples, onPcmFrame) {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("这个浏览器不能使用麦克风。");
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            sampleRate: 16000,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (error) {
        if (error && (error.name === "NotAllowedError" || error.name === "PermissionDeniedError")) {
          throw new Error("没有获得麦克风权限。");
        }
        if (error && error.name === "NotFoundError") throw new Error("没有找到可用的麦克风。");
        throw new Error("无法打开麦克风。");
      }

      audioContext = new AudioContext();
      await audioContext.resume();
      source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      resampler = createStreamingResampler(audioContext.sampleRate, 16000);
      frameWriter = createFrameWriter(5120, onPcmFrame);
      mute = audioContext.createGain();
      mute.gain.value = 0;
      accepting = true;
      const handleMono = (mono) => {
        if (!accepting) return;
        frameWriter.push(floatToPcm16(resampler.push(mono)));
      };
      if (audioContext.audioWorklet) {
        try {
          await audioContext.audioWorklet.addModule("/assets/pcm-worklet.js");
          processor = new AudioWorkletNode(audioContext, "pcm-capture");
          processor.port.onmessage = (event) => handleMono(event.data);
        } catch {
          processor = null;
        }
      }
      if (!processor) {
        processor = audioContext.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (event) => {
          const input = event.inputBuffer;
          const mono = new Float32Array(input.length);
          for (let channel = 0; channel < input.numberOfChannels; channel += 1) {
            const samples = input.getChannelData(channel);
            for (let index = 0; index < samples.length; index += 1) {
              mono[index] += samples[index] / input.numberOfChannels;
            }
          }
          handleMono(mono);
        };
      }
      source.connect(processor);
      processor.connect(mute);
      mute.connect(audioContext.destination);

      const time = new Uint8Array(analyser.fftSize);
      maxLevel = 0;
      startedAt = performance.now();
      const tick = () => {
        analyser.getByteTimeDomainData(time);
        const samples = new Float32Array(time.length);
        let sum = 0;
        for (let index = 0; index < time.length; index += 1) {
          const value = (time[index] - 128) / 128;
          samples[index] = value;
          sum += value * value;
        }
        const level = Math.sqrt(sum / time.length);
        maxLevel = Math.max(maxLevel, level);
        onSamples(samples, level);
        frame = requestAnimationFrame(tick);
      };
      tick();
    },
    async stop() {
      cancelAnimationFrame(frame);
      if (!audioContext) throw new Error("录音还没有开始。");
      source?.disconnect();
      processor?.disconnect();
      await new Promise((resolve) => setTimeout(resolve, 0));
      accepting = false;
      if (processor?.port) processor.port.onmessage = null;
      else if (processor) processor.onaudioprocess = null;
      frameWriter.push(floatToPcm16(resampler.flush()));
      const tail = frameWriter.flush();
      const result = { maxLevel, durationMs: performance.now() - startedAt, tail };
      await release();
      return result;
    },
    cancel() {
      accepting = false;
      cancelAnimationFrame(frame);
      release();
    },
  };

  async function release() {
    processor?.disconnect();
    mute?.disconnect();
    analyser?.disconnect();
    source?.disconnect();
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
    if (audioContext && audioContext.state !== "closed") await audioContext.close();
    audioContext = null;
    source = null;
    analyser = null;
    processor = null;
    mute = null;
    frameWriter = null;
    resampler = null;
  }
}

export function createFrameWriter(frameBytes, onFrame) {
  let pending = new Uint8Array(0);
  return {
    push(bytes) {
      if (!bytes.length) return;
      const joined = new Uint8Array(pending.length + bytes.length);
      joined.set(pending);
      joined.set(bytes, pending.length);
      let offset = 0;
      while (joined.length - offset >= frameBytes) {
        onFrame(joined.slice(offset, offset + frameBytes).buffer);
        offset += frameBytes;
      }
      pending = joined.slice(offset);
    },
    flush() {
      const tail = pending.slice();
      pending = new Uint8Array(0);
      return tail.byteLength ? tail.buffer : null;
    },
  };
}

function createStreamingResampler(sourceRate, targetRate) {
  const ratio = sourceRate / targetRate;
  let pending = new Float32Array(0);
  let position = 0;
  return {
    push(input) {
      const joined = new Float32Array(pending.length + input.length);
      joined.set(pending);
      joined.set(input, pending.length);
      const output = [];
      while (position + 1 < joined.length) {
        const left = Math.floor(position);
        const mix = position - left;
        output.push(joined[left] * (1 - mix) + joined[left + 1] * mix);
        position += ratio;
      }
      const consumed = Math.floor(position);
      pending = joined.slice(consumed);
      position -= consumed;
      return Float32Array.from(output);
    },
    flush() {
      const output = pending.length ? Float32Array.of(pending[0]) : new Float32Array(0);
      pending = new Float32Array(0);
      position = 0;
      return output;
    },
  };
}

function floatToPcm16(samples) {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let index = 0; index < samples.length; index += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[index]));
    view.setInt16(index * 2, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
  }
  return bytes;
}
