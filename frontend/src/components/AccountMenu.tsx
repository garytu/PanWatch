import { useState, useEffect, useRef } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Moon, Sun, Monitor, Check, LogOut, User, Stethoscope, type LucideIcon } from 'lucide-react'
import { isAuthenticated, logout } from '@panwatch/api'
import type { ThemeMode } from '@/hooks/use-theme'
import { useAvatar } from '@/hooks/use-avatar'

export interface AccountNavItem {
  to: string
  icon: LucideIcon
  label: string
}

const THEME_OPTIONS: { value: ThemeMode; icon: LucideIcon; label: string }[] = [
  { value: 'light', icon: Sun, label: '亮色' },
  { value: 'dark', icon: Moon, label: '暗色' },
  { value: 'system', icon: Monitor, label: '跟隨系統' },
]

interface AccountMenuProps {
  /** 原“更多”裡摺疊的導航項(Agent / 歷史 / 資料來源 / 設定)。 */
  navItems: AccountNavItem[]
  mode: ThemeMode
  onSetMode: (m: ThemeMode) => void
  /** 開啟「系統自檢」彈跳視窗(狀態由上層 App 託管,避免桌面/移動兩個例項重複)。 */
  onOpenSelfCheck: () => void
  /** 頭像尺寸:桌面 md,移動端 sm。 */
  size?: 'sm' | 'md'
}

/**
 * 右上角頭像區域 + 下拉選單(參考 beecount-cloud):
 * 把原“更多”導航、主題色(亮/暗/跟隨系統)、退出登入收進頭像下拉
 * (檢視日誌 / GitHub 仍在外側)。
 */
export default function AccountMenu({
  navItems,
  mode,
  onSetMode,
  onOpenSelfCheck,
  size = 'md',
}: AccountMenuProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement | null>(null)
  const location = useLocation()
  const avatar = useAvatar()
  // 僅在支援 hover 的裝置(PC)啟用懸停展開;觸屏維持點選
  const [canHover] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(hover: hover)').matches,
  )

  // 點選外部關閉
  useEffect(() => {
    const onPointerDown = (e: PointerEvent) => {
      if (open && ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  // 路由變化時關閉
  useEffect(() => {
    setOpen(false)
  }, [location.pathname])

  const avatarSize = size === 'sm' ? 'w-6 h-6' : 'w-7 h-7'
  const iconSize = size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'

  return (
    <div
      className="relative"
      ref={ref}
      onMouseEnter={canHover ? () => setOpen(true) : undefined}
      onMouseLeave={canHover ? () => setOpen(false) : undefined}
    >
      <button
        onClick={() => setOpen(v => !v)}
        className={`${avatarSize} rounded-full overflow-hidden bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm ring-1 transition-all ${
          open ? 'ring-primary/50' : 'ring-border/40 hover:ring-primary/40'
        }`}
        title="帳戶與設定"
        aria-label="帳戶與設定"
      >
        {avatar ? (
          <img src={avatar} alt="頭像" className="w-full h-full object-cover" />
        ) : (
          <User className={`${iconSize} text-white`} />
        )}
      </button>

      {open && (
        // top-full + pt-2:用透明內邊距橋接頭像與選單,hover 移入不斷開
        <div className="absolute right-0 top-full pt-2 z-50">
          <div className="w-48 rounded-xl border border-border/60 bg-card/95 backdrop-blur p-1.5 shadow-xl">
          {/* 原“更多”導航 */}
          {navItems.map(({ to, icon: Icon, label }) => {
            const isActive = location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                onClick={() => setOpen(false)}
                className={`flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  isActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </NavLink>
            )
          })}

          <div className="my-1 h-px bg-border/50" />

          {/* 主題色:亮 / 暗 / 跟隨系統 */}
          <div className="px-2.5 pt-0.5 pb-1 text-[11px] text-muted-foreground">主題</div>
          {THEME_OPTIONS.map(({ value, icon: Icon, label }) => {
            const active = mode === value
            return (
              <button
                key={value}
                onClick={() => onSetMode(value)}
                className={`flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] transition-colors ${
                  active
                    ? 'text-foreground bg-accent/40'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent/60'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
                {active && <Check className="w-3.5 h-3.5 ml-auto text-primary" />}
              </button>
            )
          })}

          <div className="my-1 h-px bg-border/50" />
          {/* 系統自檢:開啟彈跳視窗(逐項檢查資料來源/AI/通知連通性) */}
          <button
            onClick={() => {
              setOpen(false)
              onOpenSelfCheck()
            }}
            className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-foreground hover:bg-accent/60 transition-colors"
          >
            <Stethoscope className="w-3.5 h-3.5" />
            系統自檢
          </button>

          {isAuthenticated() && (
            <>
              <div className="my-1 h-px bg-border/50" />
              <button
                onClick={logout}
                className="flex w-full items-center gap-2.5 px-2.5 py-2 rounded-lg text-[12px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                <LogOut className="w-3.5 h-3.5" />
                退出登入
              </button>
            </>
          )}
          </div>
        </div>
      )}
    </div>
  )
}
