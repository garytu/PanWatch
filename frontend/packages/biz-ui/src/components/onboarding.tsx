import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { TrendingUp, Bot, Bell, CheckCircle2, ChevronRight, Sparkles } from 'lucide-react'
import { Dialog, DialogContent } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface OnboardingProps {
  open: boolean
  onComplete: () => void
  hasStocks: boolean
}

type Step = 'welcome' | 'ai' | 'notify' | 'complete'

export function Onboarding({ open, onComplete, hasStocks }: OnboardingProps) {
  const navigate = useNavigate()
  const [step, setStep] = useState<Step>('welcome')

  const handleNext = () => {
    if (step === 'welcome') {
      setStep('ai')
    } else if (step === 'ai') {
      setStep('notify')
    } else if (step === 'notify') {
      setStep('complete')
    } else {
      onComplete()
    }
  }

  const handleSkip = () => {
    onComplete()
  }

  const handleGoToSettings = () => {
    onComplete()
    navigate('/settings')
  }

  const handleGoToPortfolio = () => {
    onComplete()
    navigate('/portfolio')
  }

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onComplete()}>
      <DialogContent className="max-w-md p-0 overflow-hidden">
        {/* Progress Indicator */}
        <div className="flex items-center gap-1.5 px-6 pt-6">
          {(['welcome', 'ai', 'notify', 'complete'] as Step[]).map((s, i) => (
            <div
              key={s}
              className={`flex-1 h-1 rounded-full transition-colors ${
                i <= ['welcome', 'ai', 'notify', 'complete'].indexOf(step)
                  ? 'bg-primary'
                  : 'bg-accent/50'
              }`}
            />
          ))}
        </div>

        <div className="p-6 pt-4">
          {step === 'welcome' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <TrendingUp className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                歡迎使用盯盤俠
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                {hasStocks
                  ? '你的自選股已就緒，可以開始使用了'
                  : '我們已為你添加了 5 只熱門股票作為示例，你可以立即檢視即時行情'
                }
              </p>

              <div className="space-y-3 text-left mb-6">
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-blue-500/10 flex items-center justify-center flex-shrink-0">
                    <TrendingUp className="w-4 h-4 text-blue-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">即時行情監控</p>
                    <p className="text-[12px] text-muted-foreground">跟蹤自選股價格變動，快速發現異動</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                    <Bot className="w-4 h-4 text-primary" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">AI 智慧分析</p>
                    <p className="text-[12px] text-muted-foreground">盤後日報、異動建議、技術分析</p>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-3 rounded-xl bg-accent/30">
                  <div className="w-8 h-8 rounded-lg bg-amber-500/10 flex items-center justify-center flex-shrink-0">
                    <Bell className="w-4 h-4 text-amber-500" />
                  </div>
                  <div>
                    <p className="text-[13px] font-medium text-foreground">智慧通知推送</p>
                    <p className="text-[12px] text-muted-foreground">Telegram、企業微信等多管道推送</p>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <Button className="flex-1" onClick={handleNext}>
                  開始使用 <ChevronRight className="w-4 h-4" />
                </Button>
              </div>
              <button
                onClick={handleSkip}
                className="mt-3 text-[12px] text-muted-foreground hover:text-foreground transition-colors"
              >
                跳過引導
              </button>
            </div>
          )}

          {step === 'ai' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary flex items-center justify-center mx-auto mb-4">
                <Bot className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                配置 AI 分析
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                連線 AI 服務後，可獲得智慧分析功能
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">盤後日報自動分析</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">異動 AI 建議</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Sparkles className="w-4 h-4 text-primary" />
                  <span className="text-foreground">技術圖表分析</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                支援 OpenAI、智譜、DeepSeek 等服務商
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  稍後再說
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  前往配置
                </Button>
              </div>
            </div>
          )}

          {step === 'notify' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-amber-500 flex items-center justify-center mx-auto mb-4">
                <Bell className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                配置通知管道
              </h2>
              <p className="text-[14px] text-muted-foreground mb-4">
                配置後可收到即時推送通知
              </p>

              <div className="space-y-2 text-left mb-6 p-4 rounded-xl bg-accent/30">
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">盤中異動提醒</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">AI 分析報告推送</span>
                </div>
                <div className="flex items-center gap-2 text-[13px]">
                  <Bell className="w-4 h-4 text-amber-500" />
                  <span className="text-foreground">停利停損預警</span>
                </div>
              </div>

              <p className="text-[12px] text-muted-foreground mb-4">
                支援 Telegram、企業微信等管道
              </p>

              <div className="flex items-center gap-3">
                <Button variant="secondary" className="flex-1" onClick={handleNext}>
                  稍後再說
                </Button>
                <Button className="flex-1" onClick={handleGoToSettings}>
                  前往配置
                </Button>
              </div>
            </div>
          )}

          {step === 'complete' && (
            <div className="text-center">
              <div className="w-16 h-16 rounded-2xl bg-emerald-500 flex items-center justify-center mx-auto mb-4">
                <CheckCircle2 className="w-8 h-8 text-white" />
              </div>
              <h2 className="text-[20px] font-bold text-foreground mb-2">
                設定完成
              </h2>
              <p className="text-[14px] text-muted-foreground mb-6">
                你可以隨時在「設定」頁面修改配置
              </p>

              <div className="space-y-3">
                <Button className="w-full" onClick={() => onComplete()}>
                  進入 Dashboard
                </Button>
                <Button variant="secondary" className="w-full" onClick={handleGoToPortfolio}>
                  管理自選股
                </Button>
              </div>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
