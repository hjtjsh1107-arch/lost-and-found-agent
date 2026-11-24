import { expect, test } from '@playwright/test'
import { randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../..', import.meta.url))
const python = process.env.LAF_PYTHON || resolve(root, '.venv', 'Scripts', 'python.exe')
const fileName = 'test-umbrella.png'

test('사진 첨부, 분석, 추가 확인, 승인, 공개 검색과 로그', async ({ page, context, browser }, testInfo) => {
  const photo = testInfo.outputPath(fileName)
  execFileSync(python, ['scripts/make_sample_photo.py', photo], { cwd: root })
  const title = `시청역 우산 검증 ${randomUUID().replace(/[^a-f]/g, '').slice(0, 10)}`
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '습득물 등록' })).toBeVisible()
  await expect(page.getByText('Mock Mode')).toBeVisible()
  await page.getByRole('button', { name: 'AI 분석 시작' }).click()
  await expect(page.getByText('습득물 사진을 한 장 이상 첨부해주세요.')).toBeVisible()
  await expect(page.getByRole('button', { name: '사진 선택' })).toBeFocused()
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('button', { name: '사진 선택' }).press('Enter')
  await (await chooser).setFiles(photo)
  await expect(page.getByAltText('첨부 사진 1')).toBeVisible()
  await page.getByRole('button', { name: '사진 1 삭제' }).click()
  await expect(page.getByAltText('첨부 사진 1')).toHaveCount(0)
  const file = [...readFileSync(photo)]
  const transfer = await page.evaluateHandle(bytes => {
    const data = new DataTransfer()
    data.items.add(new File([new Uint8Array(bytes)], 'dropped-umbrella.png', { type: 'image/png' }))
    return data
  }, file)
  await page.locator('.drop-zone').dispatchEvent('drop', { dataTransfer: transfer })
  await expect(page.getByAltText('첨부 사진 1')).toBeVisible()
  await page.getByLabel('습득 지역').fill('서울시 중구')
  await page.getByLabel('습득 장소').fill('시청역 대합실')
  await page.getByLabel('습득자명').fill('비공개가상접수자')
  await page.getByLabel('보관기관').fill('시청역 안내센터')
  await page.getByLabel('추가 메모').fill('색상: 파랑, 연락 010-0000-0000')
  await page.getByRole('button', { name: 'AI 분석 시작' }).click()
  await page.getByLabel('담당자 접근 키').fill('local-mock-only')
  const queued = page.waitForResponse(response => response.url().endsWith('/staff/drafts?background=true'))
  await page.getByRole('button', { name: '접속하기' }).click()
  expect((await queued).status()).toBe(202)
  await expect(page.getByRole('heading', { name: '표준화된 물품 정보' })).toBeVisible()
  await expect(page.getByText('추가 확인 필요', { exact: true })).toBeVisible()
  const session = (await context.cookies()).find(cookie => cookie.name === 'laf_staff_session')
  expect(session?.httpOnly).toBe(true)
  expect(session?.value).not.toContain('local-mock-only')
  expect(await page.evaluate(() => document.cookie)).not.toContain('laf_staff_session')
  await page.getByRole('link', { name: '추가 정보 확인' }).click()
  await expect(page.getByText('담당자 확인 요청')).toBeVisible()
  await page.getByLabel('공고 제목').fill(title)
  await page.getByText('습득 기본 정보 수정', { exact: true }).click()
  await page.getByLabel('추가 메모').fill('검정색 확인, 연락 010-0000-0000')
  await page.getByRole('button', { name: '수정 및 재검증' }).click()
  await expect(page.getByRole('heading', { name: '공고 검토 및 승인' })).toBeVisible()
  await expect(page.getByText('검정색 확인, 연락 010-****-****', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '마스킹 사진 보기' }).click()
  const maskedPhoto = page.getByAltText('마스킹된 사진 1')
  await expect(maskedPhoto).toBeVisible()
  await expect.poll(() => maskedPhoto.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBeGreaterThan(0)
  const rectangle = await maskedPhoto.boundingBox()
  if (!rectangle) throw new Error('마스킹 대상 사진의 좌표를 확인할 수 없습니다.')
  await page.mouse.move(rectangle.x + rectangle.width / 10, rectangle.y + rectangle.height / 10)
  await page.mouse.down()
  await page.mouse.move(rectangle.x + rectangle.width / 3, rectangle.y + rectangle.height / 3)
  await page.mouse.up()
  await expect(page.locator('.mask-selection')).toBeVisible()
  await page.getByLabel('왼쪽 X').fill('0')
  await page.getByLabel('위쪽 Y').fill('0')
  await page.getByLabel('오른쪽 X').fill('40')
  await page.getByLabel('아래쪽 Y').fill('40')
  await page.getByRole('button', { name: '추가 마스킹 및 재검사' }).click()
  await expect(page.getByText('탐지 영역 1개 · 담당자 지정')).toBeVisible()
  await expect(page.getByRole('button', { name: '담당자 승인' })).toBeDisabled()
  await page.getByRole('checkbox', { name: '공개 정보와 개인정보 마스킹 결과를 검토했습니다.' }).check()
  await page.getByRole('button', { name: '담당자 승인' }).click()
  await page.getByRole('button', { name: '공고 등록', exact: true }).click()
  await expect(page.getByText('공고 등록이 완료되었습니다.')).toBeVisible()
  await expect(page.getByRole('heading', { name: title })).toBeVisible()
  const noticeId = new URL(page.url()).pathname.split('/').at(-1)
  await page.getByRole('link', { name: '실행 로그', exact: true }).click()
  await expect(page.getByRole('table')).toContainText('Publication Policy')

  const publicPage = await browser.newPage()
  await publicPage.goto(new URL('/notices', page.url()).href)
  await publicPage.getByLabel('검색어').fill(title)
  await publicPage.getByLabel('공식 대분류').selectOption({ label: '기타물품' })
  await publicPage.getByRole('button', { name: '검색', exact: true }).click()
  await expect(publicPage.locator('.result-count')).toHaveText('전체 1건')
  await publicPage.getByRole('link', { name: title, exact: true }).click()
  await expect(publicPage.getByText('시청역 대합실', { exact: true })).toBeVisible()
  await expect(publicPage.getByText('비공개가상접수자')).toHaveCount(0)
  expect((await publicPage.request.get(`/api/staff/drafts/${noticeId}`)).status()).toBe(401)
  expect((await publicPage.request.get(`/api/staff/drafts/${noticeId}/photos/1?kind=original`)).status()).toBe(401)
  const payload = await (await publicPage.request.get(`/api/notices/${noticeId}`)).text()
  expect(payload).not.toMatch(/010-0000-0000|비공개가상접수자|verification_only/)
  await publicPage.close()
  await page.getByRole('button', { name: '접속 해제' }).click()
  await expect(page.getByRole('heading', { name: '담당자 접속이 필요합니다' })).toBeVisible()
})

test('모바일에서 키보드로 이동하고 등록·검색 화면을 사용', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: '본문 바로가기' })).toBeFocused()
  await expect(page.getByRole('heading', { name: '습득물 등록' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await page.getByRole('link', { name: '통합 공고 목록', exact: true }).click()
  await expect(page.getByRole('heading', { name: '통합 습득물 공고' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
})

test('검색 필터를 유지하며 공고 목록 페이지 이동', async ({ page, request }, testInfo) => {
  const photo = testInfo.outputPath(fileName)
  execFileSync(python, ['scripts/make_sample_photo.py', photo], { cwd: root })
  const buffer = readFileSync(photo)
  const location = `검증 안내소 ${randomUUID().replace(/[^a-f]/g, '').slice(0, 10)}`
  const headers = { Authorization: 'Bearer local-mock-only' }
  for (let counter = 0; counter < 9; counter += 1) {
    const created = await request.post('/api/staff/drafts', {
      headers,
      multipart: {
        metadata: JSON.stringify({ region: '서울시 중구', location, finder: '가상 담당자',
          institution: '테스트 보관기관', found_at: '2025-02-01T12:00:00+09:00' }),
        photos: { name: fileName, mimeType: 'image/png', buffer },
      },
    })
    expect(created.ok()).toBeTruthy()
    const draft = await created.json()
    const approved = await request.post(`/api/staff/drafts/${draft.id}/approve`, {
      headers, data: { revision: draft.revision, confirmed: true },
    })
    expect(approved.ok()).toBeTruthy()
    const approval = await approved.json()
    expect((await request.post(`/api/staff/drafts/${draft.id}/publish`, {
      headers, data: { revision: approval.revision },
    })).ok()).toBeTruthy()
  }

  await page.goto('/notices')
  await page.getByLabel('검색어').fill(location)
  await page.getByLabel('공식 대분류').selectOption({ label: '기타물품' })
  await page.getByRole('button', { name: '검색' }).click()
  await expect(page.locator('.result-count')).toHaveText('전체 9건')
  await expect(page.locator('.notice-row')).toHaveCount(8)
  await page.getByRole('link', { name: '다음 페이지' }).click()
  await expect(page.getByRole('link', { name: '2 페이지' })).toHaveAttribute('aria-current', 'page')
  await expect(page.locator('.notice-row')).toHaveCount(1)
  await page.locator('.notice-row h2 a').click()
  await page.getByRole('link', { name: '목록으로' }).click()
  await expect(page.getByRole('link', { name: '2 페이지' })).toHaveAttribute('aria-current', 'page')
  await expect(page.getByLabel('검색어')).toHaveValue(location)
})

test('공식 소분류와 관찰 유형을 바꾸면 물품 필드가 바뀐다', async ({ page, request }, testInfo) => {
  const photo = testInfo.outputPath(fileName)
  execFileSync(python, ['scripts/make_sample_photo.py', photo], { cwd: root })
  const created = await request.post('/api/staff/drafts', {
    headers: { Authorization: 'Bearer local-mock-only' },
    multipart: {
      metadata: JSON.stringify({ region: '서울', location: '시험 안내소', finder: '가상 담당자',
        institution: '테스트 보관기관', found_at: '2025-02-01T12:00:00+09:00' }),
      photos: { name: fileName, mimeType: 'image/png', buffer: readFileSync(photo) },
    },
  })
  expect(created.ok()).toBeTruthy()
  const draft = await created.json()
  await page.goto(`/drafts/${draft.id}/confirm`)
  await page.getByRole('button', { name: '담당자 접속', exact: true }).first().click()
  await page.getByLabel('담당자 접근 키').fill('local-mock-only')
  await page.getByRole('button', { name: '접속하기' }).click()
  await expect(page.getByLabel('손잡이 색상')).toBeVisible()
  await page.getByLabel('물품명').fill('회색 트렌치코트')
  await page.getByLabel('관찰된 물품 유형').fill('트렌치코트')
  await page.getByLabel('관찰된 물품 유형').blur()
  await page.getByLabel('공식 대분류', { exact: true }).selectOption({ label: '의류' })
  await expect(page.getByLabel('공식 소분류', { exact: true }).getByRole('option', { name: '자동차열쇠' })).toHaveCount(0)
  await page.getByLabel('공식 소분류', { exact: true }).selectOption({ label: '기타의류' })
  await expect(page.getByLabel('사이즈')).toBeVisible()
  await expect(page.getByLabel('손잡이 색상')).toHaveCount(0)
  await page.getByLabel('사이즈').fill('M')
  await page.getByRole('button', { name: '수정 및 재검증' }).click()
  await expect(page.getByText('기타의류', { exact: true })).toBeVisible()
  await page.goto(`/drafts/${draft.id}/analysis`)
  const info = page.getByRole('heading', { name: '표준화된 물품 정보' }).locator('..').locator('..').locator('..')
  await expect(info.getByText('사이즈', { exact: true })).toBeVisible()
  await expect(info.getByText('손잡이 색상', { exact: true })).toHaveCount(0)
})
