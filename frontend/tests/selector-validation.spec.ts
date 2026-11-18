import { expect, test } from '@playwright/test'

/**
 * 选择器校验数据报告专项验收：
 * - 空分页不再误报为选择器错误，可用规则可发布并驱动数据提取；
 * - 三类问题（空分页/匹配失败/字段缺失）按规则字段区分展示；
 * - 修正只解除对应阻断，同版本复核已解除阻断不重现；
 * - 草稿/发布版本冲突只绑定一个报告编号；
 * - 报告错位/丢失/残留按缺陷处置；事务失败回滚；重复送交不重复建账。
 */
const ENDPOINT = '/api/selector'

async function createRule(page: import('@playwright/test').Page, code: string, site: string, extra: Record<string, string> = {}) {
  const values = {
    规则编号: code,
    归属站点: site,
    列表选择器: 'ul.news-list li.item',
    详情选择器: 'a.detail-link',
    字段选择器: 'title=.title;url=a.detail-link@href;time=.pub-time',
    分页参数: '',
    等待条件: '',
    字符编码: 'utf-8',
    ...extra,
  }
  const data = await page.request.post(ENDPOINT, { data: values })
  const body = await data.json()
  expect(body.ok).toBeTruthy()
  return body.entry.id as number
}

test.describe('选择器校验数据报告', () => {
  test('空分页不误报，规则可发布并驱动数据提取', async ({ page }) => {
    const id = await createRule(page, 'SELE-E2E-EMPTY', '抽取规则样例2')

    const report = await (await page.request.post(`${ENDPOINT}/${id}/validate`, { data: {} })).json()
    expect(report.ok).toBeTruthy()
    const pageItem = report.entry.items.find((i: any) => i.field === '分页参数')
    expect(pageItem.category).toBe('EMPTY_PAGINATION')
    expect(pageItem.blocking).toBe(false)
    expect(pageItem.state).toBe('提示')
    expect(report.entry.blocking).toBe(false)

    const published = await (await page.request.post(`${ENDPOINT}/${id}/publish`)).json()
    expect(published.ok).toBeTruthy()

    const drive = await (await page.request.post(`${ENDPOINT}/${id}/drive-extraction`, { data: {} })).json()
    expect(drive.ok).toBeTruthy()
    expect(drive.entry.status).toBe('已受理')

    // 重复送交不产生新编号
    const again = await (await page.request.post(`${ENDPOINT}/${id}/drive-extraction`, { data: {} })).json()
    expect(again.entry.record_no).toBe(drive.entry.record_no)
    const ledger = await (await page.request.get('/api/extractor/execution-records?report_no=' + report.entry.report_no)).json()
    expect(ledger.total).toBe(1)
  })

  test('匹配失败与字段缺失按字段分类，阻断规则不能发布', async ({ page }) => {
    const badId = await createRule(page, 'SELE-E2E-MATCH', '抽取规则样例3', { 分页参数: 'a.page-next' })
    const bad = await (await page.request.post(`${ENDPOINT}/${badId}/validate`, { data: {} })).json()
    const categories = new Set(bad.entry.items.map((i: any) => i.category))
    expect(categories.has('MATCH_FAIL')).toBeTruthy()
    expect(bad.entry.status).toBe('阻断中')
    const blockedPublish = await (await page.request.post(`${ENDPOINT}/${badId}/publish`)).json()
    expect(blockedPublish.ok).toBe(false)

    const missId = await createRule(page, 'SELE-E2E-MISS', '抽取规则样例1', {
      分页参数: 'a.page-next',
      字段选择器: 'title=.headline;url=a.detail-link@href;time=.pub-time',
    })
    const miss = await (await page.request.post(`${ENDPOINT}/${missId}/validate`, { data: {} })).json()
    const fieldItem = miss.entry.items.find((i: any) => i.field === '字段选择器')
    expect(fieldItem.category).toBe('FIELD_MISSING')
    expect(fieldItem.state).toBe('阻断')
  })

  test('页面报告弹窗按字段展示分类与阻断，复核入口可用', async ({ page }) => {
    const id = await createRule(page, 'SELE-E2E-UI', '抽取规则样例3', { 分页参数: 'a.page-next' })
    await page.goto('/selector')
    const row = page.locator(`[data-testid="row-${id}"]`)
    await row.waitFor()
    await row.getByTestId('btn-validate').click()

    const modal = page.getByTestId('report-modal')
    await expect(modal).toBeVisible()
    await expect(modal.getByTestId('report-item-分页参数')).toContainText('匹配失败')
    await expect(modal.getByTestId('report-item-列表选择器')).toContainText('阻断')
    await expect(modal.getByTestId('revalidate')).toBeVisible()

    // 完整性核对：模拟残缺送交后报告进入缺陷状态
    await modal.getByTestId('integrity-tamper').click()
    await expect(modal.getByTestId('defect-box')).toBeVisible()
  })

  test('修正生成版本边界，版本冲突只绑定一个报告', async ({ page }) => {
    const id = await createRule(page, 'SELE-E2E-VER', '抽取规则样例2')
    const first = await (await page.request.post(`${ENDPOINT}/${id}/validate`, { data: {} })).json()
    await page.request.post(`${ENDPOINT}/${id}/publish`)
    const edited = await (await page.request.put(`${ENDPOINT}/${id}`, { data: { values: { 字符编码: 'gbk' } } })).json()
    expect(edited.entry.draft_version).toBe(2)
    expect(edited.entry.published_version).toBe(1)

    const conflict = await (await page.request.get(`${ENDPOINT}/${id}/conflict`)).json()
    expect(conflict.conflict).toBe(true)

    const second = await (await page.request.post(`${ENDPOINT}/${id}/validate`, { data: {} })).json()
    expect(second.entry.report_no).not.toBe(first.entry.report_no)
    const detail = await (await page.request.get(`${ENDPOINT}/${id}`)).json()
    expect(detail.latest_report_no).toBe(second.entry.report_no)
    // 历史报告仍可追溯，但规则只绑定一个编号
    const history = await (await page.request.get(`${ENDPOINT}/${id}/reports`)).json()
    expect(history.total).toBe(2)
  })

  test('事务提交失败回滚并保留上一版本', async ({ page }) => {
    const id = await createRule(page, 'SELE-E2E-TX', '抽取规则样例2')
    const report = await (await page.request.post(`${ENDPOINT}/${id}/validate`, { data: {} })).json()
    await page.request.post(`${ENDPOINT}/${id}/publish`)
    const before = await (await page.request.get('/api/extractor/execution-records')).json()
    const failed = await (
      await page.request.post(`${ENDPOINT}/${id}/drive-extraction`, { data: { fail_commit: true } })
    ).json()
    expect(failed.ok).toBe(false)
    expect(failed.message).toContain('回滚')
    const after = await (await page.request.get('/api/extractor/execution-records')).json()
    expect(after.total).toBe(before.total)
    const stored = await (await page.request.get(`${ENDPOINT}/reports/${report.entry.report_no}`)).json()
    expect(stored.execution_record_no).toBeUndefined()
  })
})
