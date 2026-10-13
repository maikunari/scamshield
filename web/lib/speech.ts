/** Read-aloud via the browser Speech Synthesis API. No-ops where it is unavailable. */

export function speechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
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
