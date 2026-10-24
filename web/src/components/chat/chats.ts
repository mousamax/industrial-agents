// Chat instances live here, outside React: messages survive the panel being hidden (mode switch, sheet
// collapse), and map sync keeps running while it is. One conversation per building; switching building
// starts a fresh one.
import { Chat } from '@ai-sdk/react'
import { DefaultChatTransport, getToolName, isToolUIPart, type UIMessage } from 'ai'
import { useSyncExternalStore } from 'react'
import { chatUrl } from '../../api/client'
import type { ShowRoute } from '../../api/types'
import { useApp } from '../../store'
import { splitAnswer } from './text'
import { resolveMessage, toolEffect, type ToolEffect } from './viewSync'

export type ChatMessage = UIMessage
export type BuildingChat = Chat<ChatMessage>

interface Entry {
  chat: BuildingChat
  dispose: () => void
}

const entries = new Map<string, Entry>()
const listeners = new Set<() => void>()
const notify = () => listeners.forEach((l) => l())

function textOf(message: ChatMessage): string {
  return message.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n')
}

function createEntry(buildingId: string): Entry {
  const chat = new Chat<ChatMessage>({
    transport: new DefaultChatTransport({ api: chatUrl(buildingId) }),
  })
  const effects = new Map<string, ToolEffect>()
  const finalized = new Set<string>()
  let shownRoute: ShowRoute | null = null

  const showRoute = (route: ShowRoute) => {
    if (route === shownRoute) return
    shownRoute = route
    useApp.getState().setRoute(route)
  }

  // Each tool output is applied once, as soon as it arrives.
  const onMessages = () => {
    const app = useApp.getState()
    for (const message of chat.messages) {
      if (message.role !== 'assistant') continue
      for (const part of message.parts) {
        if (!isToolUIPart(part) || effects.has(part.toolCallId)) continue
        if (part.state !== 'output-available' && part.state !== 'output-error') continue
        const effect =
          part.state === 'output-available'
            ? toolEffect(getToolName(part), part.input, part.output, app.index)
            : toolEffect(getToolName(part), part.input, undefined, app.index, part.errorText)
        effects.set(part.toolCallId, effect)
        if (effect.views.length) {
          for (const cmd of effect.views) {
            if (cmd.kind === 'route') showRoute(cmd)
            else app.applyView(cmd)
          }
        } else if (effect.route) showRoute(effect.route)
        else if (effect.unreachable) app.setUnreachable(effect.unreachable)
        else if (effect.highlight.length) app.setHighlight(effect.highlight, effect.label)
      }
    }
  }

  // When an answer is complete, settle on the route / highlight the final text actually refers to.
  const onStatus = () => {
    if (chat.status !== 'ready') return
    const message = chat.lastMessage
    if (!message || message.role !== 'assistant' || finalized.has(message.id)) return
    finalized.add(message.id)
    const app = useApp.getState()
    const messageEffects = message.parts.flatMap((p) =>
      isToolUIPart(p) && effects.has(p.toolCallId) ? [effects.get(p.toolCallId)!] : [],
    )
    const { body, answer } = splitAnswer(textOf(message))
    const res = resolveMessage(messageEffects, body, answer, app.index)
    if (res.routeAt !== null) {
      const route = messageEffects[res.routeAt].route!
      const current = useApp.getState().highlight.ids
      if (res.highlight.length || current.length) {
        app.setHighlight(res.highlight) // destination things, or clear stale search hits
        shownRoute = null // re-apply so the route (not the highlight) gets the camera
      }
      showRoute(route)
    } else if (res.unreachable) app.setUnreachable(res.unreachable)
    else if (res.highlight.length) app.setHighlight(res.highlight)
  }

  const unsubscribe = [chat['~registerMessagesCallback'](onMessages), chat['~registerStatusCallback'](onStatus)]
  return {
    chat,
    dispose: () => {
      unsubscribe.forEach((u) => u())
      void chat.stop()
    },
  }
}

function getEntry(buildingId: string): Entry {
  let entry = entries.get(buildingId)
  if (!entry) {
    // Fresh conversation when the building changes: drop the previous building's chat.
    for (const [id, other] of entries) {
      other.dispose()
      entries.delete(id)
    }
    entry = createEntry(buildingId)
    entries.set(buildingId, entry)
  }
  return entry
}

/** Synchronously get or create the BuildingChat for a buildingId. */
export function getBuildingChat(buildingId: string): BuildingChat {
  const chat = getEntry(buildingId).chat
  notify()
  return chat
}

/** Throw away this building's conversation and start a new one. */
export function resetChat(buildingId: string): void {
  entries.get(buildingId)?.dispose()
  entries.delete(buildingId)
  getEntry(buildingId)
  notify()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The Chat for a building (stable across renders and remounts; changes on resetChat). */
export function useBuildingChat(buildingId: string | null): BuildingChat | null {
  return useSyncExternalStore(subscribe, () => (buildingId ? getEntry(buildingId).chat : null))
}
