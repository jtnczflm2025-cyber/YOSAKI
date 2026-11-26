const quarters = ['第一', '第二', '第三', '第四']

export function isoWeekBounds(period: string | null): [string, string] | null {
  const match = /^(\d{4})-W(\d{2})$/.exec(period || '')
  if (!match) return null
  const jan4 = new Date(Date.UTC(Number(match[1]), 0, 4))
  const monday = new Date(jan4)
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() + 6) % 7) + (Number(match[2]) - 1) * 7)
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  return [monday.toISOString().slice(0, 10), sunday.toISOString().slice(0, 10)]
}

export function quarterName(month: number) {
  return quarters[Math.floor((month - 1) / 3)] + '季度'
}

export function periodLabel(period: string | null) {
  if (!period) return '时间未核实'
  const quarter = /^(\d{4})-Q([1-4])$/.exec(period)
  if (quarter) {
    const number = Number(quarter[2])
    return `${quarter[1]} 年${quarters[number - 1]}季度（${number * 3 - 2}—${number * 3} 月）`
  }
  const half = /^(\d{4})-H([12])$/.exec(period)
  if (half) return `${half[1]} 年${half[2] === '1' ? '上半年（1—6 月）' : '下半年（7—12 月）'}`
  const week = isoWeekBounds(period)
  if (week) return `${period.slice(0, 4)} 年第 ${Number(period.slice(6))} 周（${week[0].slice(5)}—${week[1].slice(5)}）`
  const month = /^(\d{4})-(\d{2})$/.exec(period)
  if (month) return `${month[1]} 年 ${Number(month[2])} 月`
  return /^\d{4}$/.test(period) ? `${period} 年全年` : period
}
