import { useRef, useState, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ShareCardDialogProps {
  open: boolean
  onClose: () => void
  /** 匯出 PNG 檔名(不含副檔名)。 */
  filename: string
  /** 卡片固定寬度,預設 640。 */
  width?: number
  /** 卡片正臉內容,由各業務卡傳入。顏色須顯式內聯,勿依賴主題 CSS 變數。 */
  children: ReactNode
}

/**
 * 分享卡通用外殼:統一的 Dialog + 固定寬度卡片容器 + 品牌頁尾 + 「下載圖片」按鈕。
 *
 * 設計要點:
 * - 卡片容器固定寬度(預設 640px),自帶白→#f8fafc 漸變背景、圓角、內邊距、系統字型、顯式深色文字,
 *   保證匯出 PNG 在任何主題(亮/暗)下都一致。各業務卡只需提供「正臉」children。
 * - 頁尾(免責 + 盯盤俠 PanWatch · github 引流行)由外殼統一渲染,作為全體分享卡的一致性錨點。
 * - 「下載圖片」用 html-to-image 的 toPng(pixelRatio:2, cacheBust:true)匯出為 ${filename}.png。
 */
export default function ShareCardDialog({
  open,
  onClose,
  filename,
  width = 640,
  children,
}: ShareCardDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${filename}.png`
      link.href = dataUrl
      link.click()
    } catch (e) {
      alert(e instanceof Error ? `圖片生成失敗:${e.message}` : '圖片生成失敗,請重試')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>分享圖片</DialogTitle>
          <DialogDescription>匯出一張乾淨的卡片,可分享到雪球 / 微信群。</DialogDescription>
        </DialogHeader>

        {/* 預覽區:外層用主題背景,內層卡片自帶顯式配色 */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* 匯出卡片:固定寬度,所有顏色顯式內聯,不依賴主題 CSS 變數 */}
          <div
            ref={cardRef}
            style={{
              width,
              boxSizing: 'border-box',
              background: 'linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
              borderRadius: 24,
              padding: '32px 36px',
              border: '1px solid #e2e8f0',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
              color: '#0f172a',
            }}
          >
            {/* 業務卡正臉 */}
            {children}

            {/* 分割線 */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* 頁尾:免責 + 品牌引流行(全體分享卡一致) */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              僅供參考,不構成投資建議
            </div>
            <div
              style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13.5,
                fontWeight: 700,
                color: '#0f172a',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 22,
                  height: 22,
                  borderRadius: 6,
                  background: '#0f172a',
                  color: '#ffffff',
                  fontSize: 13,
                  fontWeight: 900,
                  flexShrink: 0,
                }}
              >
                盯
              </span>
              <span>盯盤俠 PanWatch</span>
              <span style={{ color: '#cbd5e1', fontWeight: 400 }}>·</span>
              <span style={{ color: '#64748b', fontWeight: 500, fontSize: 12.5 }}>
                github.com/TNT-Likely/PanWatch
              </span>
            </div>
          </div>
        </div>

        {/* 操作區 */}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button variant="outline" size="sm" className="h-9" onClick={onClose} disabled={busy}>
            關閉
          </Button>
          <Button size="sm" className="h-9" onClick={() => void handleDownload()} disabled={busy}>
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ImageDown className="w-3.5 h-3.5" />
            )}
            {busy ? '生成中…' : '下載圖片'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
