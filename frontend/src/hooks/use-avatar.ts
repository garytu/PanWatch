import { useState, useEffect } from 'react'
import { fetchAPI } from '@panwatch/api'

const EVENT = 'panwatch:avatar-changed'

// 僅 SPA 會話內的記憶體快取(避免一次會話內重複請求)。
// 真正的持久化在後端 DB(data/panwatch.db 的 ui_avatar),重新整理後會重新從後端拉取。
let cache: string | null = null
let inflight: Promise<string> | null = null

function load(): Promise<string> {
  if (cache !== null) return Promise.resolve(cache)
  if (!inflight) {
    inflight = fetchAPI<{ value: string }>('/settings/avatar')
      .then(r => {
        cache = r?.value || ''
        return cache as string
      })
      .catch(() => {
        cache = ''
        return ''
      })
      .finally(() => {
        inflight = null
      })
  }
  return inflight
}

/**
 * 儲存頭像(傳空字串=清空):後端把圖片落成 data/avatars 檔案、DB 僅記檔名;
 * 本地廣播即時更新。注意 cache 存的是 data URL(GET 也返回 data URL)。
 */
export async function saveAvatar(value: string): Promise<void> {
  await fetchAPI('/settings/avatar', { method: 'PUT', body: JSON.stringify({ value }) })
  cache = value
  window.dispatchEvent(new CustomEvent<string>(EVENT, { detail: value }))
}

/** 當前頭像(data URL 或圖片地址)。來源為後端 DB;跨元件即時同步。 */
export function useAvatar(): string {
  const [avatar, setAvatar] = useState<string>(cache ?? '')
  useEffect(() => {
    let alive = true
    load().then(v => {
      if (alive) setAvatar(v)
    })
    const onChange = (e: Event) => setAvatar((e as CustomEvent<string>).detail ?? '')
    window.addEventListener(EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(EVENT, onChange)
    }
  }, [])
  return avatar
}

/**
 * 把上傳的圖片檔案壓縮為 size×size 的方形 JPEG data URL(居中裁剪),
 * 控制體積(約 10-20KB),避免大 base64 撐爆 DB 儲存。
 */
export function fileToAvatarDataUrl(file: File, size = 128): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('讀取檔案失敗'))
    reader.onload = () => {
      const img = new Image()
      img.onerror = () => reject(new Error('圖片解析失敗'))
      img.onload = () => {
        const canvas = document.createElement('canvas')
        canvas.width = size
        canvas.height = size
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          reject(new Error('canvas 不可用'))
          return
        }
        const scale = Math.max(size / img.width, size / img.height)
        const w = img.width * scale
        const h = img.height * scale
        ctx.drawImage(img, (size - w) / 2, (size - h) / 2, w, h)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = reader.result as string
    }
    reader.readAsDataURL(file)
  })
}
