// "Read aloud" in the ElevenLabs voice of the live agent (GET /api/voice/tts streams MP3 as it renders), with
// the browser's own speech as the fallback when the backend has no ElevenLabs key or the request fails.
import { API } from '../../api/client'

let voiceStatus: Promise<boolean> | null = null

/** Whether the backend has ElevenLabs configured (asked once per page load). */
export function voiceAvailable(): Promise<boolean> {
  voiceStatus ??= fetch(`${API}/voice/status`)
    .then((r) => (r.ok ? (r.json() as Promise<{ live: boolean }>) : { live: false }))
    .then((s) => s.live)
    .catch(() => false)
  return voiceStatus
}

export async function fetchVoiceSession(): Promise<{ token: string; agent_id: string }> {
  const res = await fetch(`${API}/voice/session`)
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `voice session: ${res.status}`)
  return res.json()
}

let current: HTMLAudioElement | null = null
let generation = 0

function browserSpeak(text: string, onEnd: () => void) {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return onEnd()
  window.speechSynthesis.cancel()
  const u = new SpeechSynthesisUtterance(text)
  u.rate = 1.02
  u.onend = onEnd
  u.onerror = onEnd
  window.speechSynthesis.speak(u)
}

export function stopSpeaking() {
  generation++
  current?.pause()
  current = null
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel()
}

/** Speak `text`; `onEnd` runs once when it finishes, fails or is stopped by the next call. */
export function speak(text: string, onEnd: () => void) {
  stopSpeaking()
  const mine = ++generation
  const done = () => mine === generation && onEnd()
  void voiceAvailable().then((live) => {
    if (mine !== generation) return
    if (!live) return browserSpeak(text, done)
    const audio = new Audio(`${API}/voice/tts?text=${encodeURIComponent(text.slice(0, 1200))}`)
    current = audio
    let fellBack = false
    const fallback = () => {
      if (fellBack || mine !== generation) return
      fellBack = true
      browserSpeak(text, done)
    }
    audio.onended = done
    audio.onerror = fallback
    audio.play().catch(fallback)
  })
}
