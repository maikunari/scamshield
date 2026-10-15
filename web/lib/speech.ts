/** Read-aloud via the browser Speech Synthesis API. No-ops where it is unavailable. */

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

/**
 * Calls `onChange(hasVoices)` now and again whenever the browser's voice list changes. Browsers load voices
 * asynchronously (the list is often empty at first), and some devices have none at all, so a read-aloud button
 * waits for this instead of trusting `speechSupported()`. Returns a cleanup function.
 */
export function watchVoices(onChange: (hasVoices: boolean) => void): () => void {
  if (!speechSupported()) {
    onChange(false);
    return () => {};
  }
  const synth = window.speechSynthesis;
  const check = () => onChange(synth.getVoices().length > 0);
  check();
  synth.addEventListener("voiceschanged", check);
  return () => synth.removeEventListener("voiceschanged", check);
}

export function stopSpeaking(): void {
  if (speechSupported()) window.speechSynthesis.cancel();
}

/** Speak `text`; resolves when done or cancelled. */
export function speak(text: string, lang = "en-US"): Promise<void> {
  return new Promise((resolve) => {
    if (!speechSupported()) return resolve();
    const synth = window.speechSynthesis;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = lang;
    u.rate = 0.9; // a touch slower than default for older listeners
    u.pitch = 1;
    const voice = synth.getVoices().find((v) => v.lang.replace("_", "-").toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()));
    if (voice) u.voice = voice;
    u.onend = () => resolve();
    u.onerror = () => resolve();
    synth.speak(u);
  });
}
