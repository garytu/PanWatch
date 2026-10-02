import { useState, useEffect } from 'react'

/** 使用者選擇的主題模式(system = 跟隨系統)。 */
export type ThemeMode = 'light' | 'dark' | 'system'
/** 實際生效的主題(system 解析後的結果)。 */
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'panwatch-theme'

function readMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readMode)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  // 跟隨系統:監聽 OS 主題變化,即時反映
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // 生效主題:顯式 light/dark 直接用,system 則跟隨當前系統
  const theme: Theme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(theme)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [theme, mode])

  // 相容舊呼叫:在亮/暗間切換(會把模式落為顯式 light/dark)
  const toggleTheme = () => setMode(theme === 'dark' ? 'light' : 'dark')

  return { theme, mode, setMode, toggleTheme }
}
