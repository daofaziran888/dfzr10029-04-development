import { expect, test } from '@playwright/test'

test('维度页：按数据集与层级路径检索维度成员映射表', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/dimension')
  await expect(page.getByTestId('member-panel')).toBeVisible()

  await page.getByTestId('member-filter-dataset').fill('销售明细')
  await page.getByTestId('member-filter-path').fill('华东')
  await page.getByTestId('member-query').click()
  const resultRows = page.locator('[data-testid="member-panel"] tbody tr')
  await expect(resultRows.first()).toBeVisible()
  expect(await resultRows.count()).toBeGreaterThanOrEqual(2)
  await expect(resultRows.first()).toContainText('销售明细数据集')
  await expect(resultRows.first()).toContainText('映射V2')

  // 两个来源输入都在同一份映射结果里
  const bodyText = await page.locator('[data-testid="member-panel"] tbody').innerText()
  expect(bodyText).toContain('分析维度')
  expect(bodyText).toContain('关联数据集')

  // 同名成员冲突场景：上海在当前版本为父级变更
  await page.getByTestId('member-filter-path').fill('')
  await page.getByTestId('member-filter-member').fill('上海')
  await page.getByTestId('member-query').click()
  await expect(page.locator('[data-testid="member-panel"] tbody').first()).toContainText('父级变更')

  await page.getByTestId('member-reset').click()
  await expect(page.locator('[data-testid="member-panel"] tbody tr')).toHaveCount(0)
  expect(browserErrors).toEqual([])
})

test('维度成员映射表页：筛选、版本边界、明细与来源、同步、导出', async ({ page }) => {
  const browserErrors: string[] = []
  page.on('pageerror', (e) => browserErrors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) browserErrors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/dimension_member')
  await expect(page.getByTestId('chain-panel')).toBeVisible()
  const bodyRows = page.locator('tbody tr[data-testid^="row-"]')
  await expect(bodyRows.first()).toBeVisible()
  expect(await bodyRows.count()).toBeGreaterThanOrEqual(10)

  // 按数据集 + 层级路径筛选定位（默认跨全部版本：V1/V2 各两处来源）
  await page.getByTestId('filter-关联数据集').fill('销售明细数据集')
  await page.getByTestId('filter-层级路径').fill('华北/北京')
  await page.getByTestId('query').click()
  await expect(bodyRows).toHaveCount(4)
  await expect(bodyRows.first()).toContainText('全国/华北/北京')

  // 锁定单一映射版本后只剩两处来源输入
  await page.getByTestId('filter-版本').selectOption({ label: '映射V2（生效中）' })
  await page.getByTestId('query').click()
  await expect(bodyRows).toHaveCount(2)
  await expect(bodyRows.first()).toContainText('映射V2')
  await page.getByTestId('reset').click()
  await expect.poll(() => bodyRows.count()).toBeGreaterThan(10)

  // 版本边界：切到 V1 能看到历史引用冲突行
  await page.getByTestId('filter-版本').selectOption({ label: '映射V1（已归档）' })
  await page.getByTestId('query').click()
  const v1Rows = page.locator('tbody tr[data-testid^="row-"]')
  expect(await v1Rows.count()).toBeGreaterThan(0)
  await expect(page.locator('tbody')).toContainText('历史引用')
  await page.getByTestId('reset').click()

  // 明细弹窗展示当前归属路径与维度版本
  await page.getByTestId('detail-1').click()
  await expect(page.getByTestId('detail-modal')).toBeVisible()
  await expect(page.locator('[data-testid="detail-modal"]')).toContainText('层级路径')
  await expect(page.locator('[data-testid="detail-modal"]')).toContainText('维度版本')
  await page.locator('[data-testid="detail-modal"] .btn.primary').click()
  await expect(page.getByTestId('detail-modal')).toHaveCount(0)

  // 另一处来源：两处来源输入同版本同路径
  await page.getByTestId('source-1').click()
  await expect(page.getByTestId('source-modal')).toBeVisible()
  const sourceRows = page.locator('[data-testid="source-modal"] tbody tr')
  await expect(sourceRows).toHaveCount(2)
  await expect(page.locator('[data-testid="source-modal"]')).toContainText('分析维度')
  await expect(page.locator('[data-testid="source-modal"]')).toContainText('关联数据集')
  await page.locator('[data-testid="source-modal"] .btn.primary').click()

  // 同步：来源未变化时沿用已确认版本，不产生新版本
  const versionChipsBefore = await page.locator('[data-testid="version-strip"] .version-chip').count()
  await page.getByTestId('sync').click()
  await expect(page.locator('.page-foot')).toContainText('沿用')
  const versionChipsAfter = await page.locator('[data-testid="version-strip"] .version-chip').count()
  expect(versionChipsAfter).toBe(versionChipsBefore)

  // 调用链核对
  await page.getByTestId('chain-check').click()
  await expect(page.locator('[data-testid="chain-panel"]')).toContainText('一致')

  // 导出
  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('export').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('dimension_member.csv')

  expect(browserErrors).toEqual([])
})
