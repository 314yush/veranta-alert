import type { ReactNode } from 'react'

const BOT_URL = import.meta.env.VITE_TELEGRAM_BOT_URL || 'https://t.me/veranta_alertsbot'
const BOT_LABEL = BOT_URL.replace('https://', '')

function TelegramIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.18 1.898-.962 6.502-1.36 8.627-.168.9-.499 1.201-.82 1.23-.698.064-1.226-.461-1.901-.903-1.056-.693-1.653-1.124-2.678-1.8-1.185-.78-.417-1.21.258-1.91.177-.184 3.247-2.977 3.307-3.23.007-.032.014-.15-.056-.212s-.174-.041-.249-.024c-.106.024-1.793 1.14-5.061 3.345-.48.33-.913.49-1.302.48-.428-.008-1.252-.241-1.865-.44-.752-.245-1.349-.374-1.297-.789.027-.216.325-.437.893-.663 3.498-1.524 5.83-2.529 6.998-3.014 3.332-1.386 4.025-1.627 4.476-1.635z" />
    </svg>
  )
}

function BrandMark({
  size = 'md',
  className = '',
}: {
  size?: 'sm' | 'md'
  className?: string
}) {
  const px = size === 'sm' ? 28 : 36
  return (
    <span className={`inline-flex items-center gap-2 pb-1.5 ${className}`.trim()}>
      <img
        src="/brand/dino.png"
        alt=""
        width={px}
        height={px}
        className={`block shrink-0 object-contain ${size === 'sm' ? 'h-7 w-7 rounded-md' : 'h-9 w-9 rounded-lg'}`}
      />
      <span className="relative">
        <span
          className={`wordmark block leading-none ${size === 'sm' ? 'text-[15px]' : 'text-lg'}`}
        >
          veranta alerts
        </span>
        <svg
          className={`pointer-events-none absolute left-0 text-accent ${size === 'sm' ? '-bottom-[6px] h-[6px] w-[7.6rem]' : '-bottom-[7px] h-[7px] w-[9.1rem]'}`}
          viewBox="0 0 148 8"
          fill="none"
          aria-hidden="true"
        >
          <path
            d="M2 5.1C14 2.2 26 6.8 38 4.6c12-2.2 22 3 35 .6 13-2.4 24 2.4 36-.4 11-2.6 22 2 37-.8"
            stroke="currentColor"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
        </svg>
      </span>
    </span>
  )
}

function CtaLink({
  children,
  href,
  className = '',
}: {
  children: ReactNode
  href: string
  className?: string
}) {
  const external = href.startsWith('http')
  return (
    <a
      href={href}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-full bg-accent px-5 py-2.5 text-sm font-semibold text-ink transition hover:bg-cream ${className}`}
      {...(external
        ? { target: '_blank', rel: 'noopener noreferrer' }
        : undefined)}
    >
      {children}
    </a>
  )
}

function AlertMock() {
  return (
    <div className="animate-fade-up-delay-2 relative mx-auto w-full max-w-[280px] sm:max-w-[292px]">
      <div className="rounded-[1.85rem] bg-[#1c1e24] p-[3px] shadow-[0_24px_64px_rgba(0,0,0,0.55)]">
        <div className="overflow-hidden rounded-[1.7rem] bg-[#0a0b0d]">
          <div className="flex items-center justify-between px-5 pt-3.5 text-[11px] tabular-nums text-mist/70">
            <span>9:41</span>
            <span>LTE</span>
          </div>

          <div className="flex items-center gap-2.5 px-4 pb-6 pt-3">
            <img
              src="/brand/dino.png"
              alt=""
              width={36}
              height={36}
              className="h-9 w-9 rounded-full object-cover"
            />
            <div className="min-w-0">
              <p className="wordmark truncate text-[15px] leading-none">
                veranta alerts
              </p>
              <p className="mt-1 text-[11px] leading-none text-mist">bot</p>
            </div>
          </div>

          <div className="min-h-[280px] px-4 pb-10">
            <div className="max-w-[208px] rounded-2xl rounded-tl-sm bg-[#1c1f27] px-3.5 py-3">
              <p className="text-[13px] font-semibold tracking-tight text-fog">
                ⚠️ BTC LONG
              </p>
              <p className="mt-1 text-[12px] leading-snug text-mist">
                −$1,240 · 23 pts to liq
              </p>
            </div>
            <div className="mt-1.5 flex max-w-[208px] overflow-hidden rounded-xl bg-[#1c1f27] text-center text-[12px] font-medium text-accent">
              <span className="flex-1 py-2">Positions</span>
              <span className="w-px self-stretch bg-white/[0.08]" aria-hidden="true" />
              <span className="flex-1 py-2">Open Veranta</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

const faqs = [
  {
    q: 'Do I need to share a private key?',
    a: 'No. Paste a Base address. Never a seed, key, or signature.',
  },
  {
    q: 'Which chain is this for?',
    a: 'Veranta perps on Base.',
  },
  {
    q: 'Is this an official Veranta product?',
    a: 'No. Independent tool on the public SDK — not the Veranta team.',
  },
  {
    q: 'Can an alert miss a liquidation?',
    a: 'Yes. This is a watchdog, not a guarantee.',
  },
  {
    q: 'What does the bot watch?',
    a: 'Near-liq, TP/SL proximity, and best-effort fills. Tune with /settings.',
  },
  {
    q: 'How do I stop it?',
    a: '/mute pauses. /stop unsubscribes.',
  },
] as const

export default function App() {
  return (
    <div className="relative min-h-screen overflow-x-hidden">
      <a
        href="#what"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-accent focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-ink"
      >
        Skip to content
      </a>

      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[480px] bg-[radial-gradient(ellipse_at_top,_rgba(124,255,196,0.08),_transparent_55%)]"
      />

      <header className="sticky top-0 z-40 border-b border-ink-border/60 bg-ink/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-5xl items-center justify-between gap-3 px-5 py-3 sm:px-8">
          <a
            href="#what"
            className="flex min-h-11 items-center"
            aria-label="veranta alerts"
          >
            <BrandMark />
          </a>
          <a
            href={BOT_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center gap-1.5 rounded-full px-3.5 text-xs font-medium text-fog transition hover:text-accent"
          >
            <TelegramIcon className="h-3.5 w-3.5" />
            Open bot
          </a>
        </div>
      </header>

      <main className="relative">
        <section
          id="what"
          className="mx-auto grid max-w-5xl scroll-mt-20 gap-12 px-5 pb-20 pt-12 sm:px-8 sm:pb-24 sm:pt-16 lg:grid-cols-[1.15fr_0.85fr] lg:items-center lg:gap-16 lg:pb-28 lg:pt-20"
        >
          <div>
            <h1 className="animate-fade-up max-w-[16ch] text-[2.15rem] font-extrabold leading-[1.05] text-white sm:text-5xl sm:leading-[1.02]">
              Don&apos;t find out you got liquidated from the chart.
            </h1>
            <p className="animate-fade-up-delay mt-5 max-w-[36ch] text-[15px] leading-relaxed text-mist sm:text-base">
              Telegram pings when your Veranta perps get close to liquidation.
            </p>
            <p className="animate-fade-up-delay mt-4 text-sm text-mist/75">
              Liq, TP, SL. Watch-only.
            </p>
            <div className="animate-fade-up-delay-2 mt-8 flex flex-col gap-4 sm:flex-row sm:items-center">
              <CtaLink href={BOT_URL} className="w-full sm:w-auto">
                <TelegramIcon className="h-4 w-4" />
                Open Telegram bot
              </CtaLink>
              <a
                href="#setup"
                className="inline-flex min-h-11 items-center justify-center text-sm text-mist transition hover:text-cream"
              >
                How it works
              </a>
            </div>
          </div>
          <AlertMock />
        </section>

        <section
          id="setup"
          className="scroll-mt-20 border-y border-ink-border/80"
        >
          <div className="mx-auto max-w-5xl px-5 py-12 sm:px-8 sm:py-16">
            <h2 className="text-[1.5rem] font-bold text-white sm:text-3xl">
              Under a minute
            </h2>
            <ol className="mt-8 grid gap-8 sm:grid-cols-3 sm:gap-10">
              <li>
                <p className="font-display text-xs font-semibold tracking-[0.16em] text-accent">
                  01
                </p>
                <h3 className="mt-2 text-base font-semibold tracking-tight text-white">
                  Open the bot
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-mist">
                  Tap Start on{' '}
                  <a
                    href={BOT_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-fog underline decoration-ink-border underline-offset-2 hover:text-accent hover:decoration-accent"
                  >
                    {BOT_LABEL}
                  </a>
                </p>
              </li>
              <li>
                <p className="font-display text-xs font-semibold tracking-[0.16em] text-accent">
                  02
                </p>
                <h3 className="mt-2 text-base font-semibold tracking-tight text-white">
                  Send your Base wallet
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-mist">
                  The 0x that trades on Veranta.
                </p>
              </li>
              <li>
                <p className="font-display text-xs font-semibold tracking-[0.16em] text-accent">
                  03
                </p>
                <h3 className="mt-2 text-base font-semibold tracking-tight text-white">
                  Stay pinged
                </h3>
                <p className="mt-1.5 text-sm leading-relaxed text-mist">
                  <code className="font-mono text-[13px] text-fog">
                    /positions
                  </code>
                </p>
              </li>
            </ol>
          </div>
        </section>

        <section
          id="start"
          className="mx-auto max-w-5xl scroll-mt-20 px-5 py-12 sm:px-8 sm:py-16"
        >
          <h2 className="text-[1.5rem] font-bold text-white sm:text-3xl">
            Get started
          </h2>
          <p className="mt-2 max-w-[32ch] text-[15px] leading-relaxed text-mist">
            Open the bot. Paste a wallet.
          </p>
          <CtaLink href={BOT_URL} className="mt-6">
            <TelegramIcon className="h-4 w-4" />
            Open Telegram bot
          </CtaLink>
        </section>

        <section
          id="faq"
          className="mx-auto max-w-5xl scroll-mt-20 px-5 pb-14 sm:px-8 sm:pb-16"
        >
          <h2 className="text-xl font-bold text-white sm:text-2xl">FAQ</h2>
          <div className="mt-5 divide-y divide-ink-border overflow-hidden rounded-2xl border border-ink-border/80">
            {faqs.map((item) => (
              <details key={item.q} className="group">
                <summary className="cursor-pointer px-4 py-1 sm:px-5">
                  <span className="flex min-h-12 items-center justify-between gap-4 py-3 text-[15px] font-medium leading-snug tracking-tight text-fog">
                    {item.q}
                    <svg
                      className="h-4 w-4 shrink-0 text-mist transition-transform duration-200 group-open:rotate-45"
                      viewBox="0 0 16 16"
                      fill="none"
                      aria-hidden="true"
                    >
                      <path
                        d="M8 3v10M3 8h10"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                      />
                    </svg>
                  </span>
                </summary>
                <p className="px-4 pb-4 text-sm leading-relaxed text-mist sm:px-5">
                  {item.a}
                </p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="border-t border-ink-border px-5 pb-[max(2rem,env(safe-area-inset-bottom))] pt-8 sm:px-8">
        <div className="mx-auto flex max-w-5xl flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <BrandMark size="sm" />
            <p className="mt-1.5 max-w-[36ch] text-xs leading-relaxed text-mist/80">
              Watch-only Telegram watchdog for Veranta perps on Base. Not
              affiliated with Veranta.
            </p>
          </div>
          <nav className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-mist">
            <a
              href={BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-fog"
            >
              Open bot
            </a>
            <a href="#setup" className="hover:text-fog">
              Setup
            </a>
            <a href="#faq" className="hover:text-fog">
              FAQ
            </a>
            <a
              href="https://veranta.xyz"
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-fog"
            >
              Veranta
            </a>
          </nav>
        </div>
        <p className="mx-auto mt-8 max-w-5xl border-t border-ink-border pt-6 text-xs leading-relaxed text-mist/70">
          Watch-only notifications. Not financial advice. Trading perpetual
          futures is risky — you can lose your collateral. © 2026
        </p>
      </footer>
    </div>
  )
}
