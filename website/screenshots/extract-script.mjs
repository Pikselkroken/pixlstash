#!/usr/bin/env node
// Reads the "script" off the marketing site: every screenshot/illustration the
// website shows, with the label (<h4>), alt text, and caption (<p>) that
// describe what the picture is meant to demonstrate. Emits script.json, which
// the screenshot-repro Playwright harness consumes to know which app scenes to
// reproduce with the current UI.
//
// Pure Node, no deps: a lightweight block parser over the static HTML. Run:
//   node website/screenshots/extract-script.mjs
import { readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const siteDir = join(here, '..')

const stripTags = (s) =>
  s
    .replace(/<[^>]+>/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()

const attr = (tag, name) => {
  const m = tag.match(new RegExp(`${name}\\s*=\\s*"([^"]*)"`, 'i'))
  return m ? m[1] : null
}

const assetName = (src) => (src ? src.replace(/^.*assets\//, '') : null)

function blockText(block) {
  const h4 = block.match(/<h4[^>]*>([\s\S]*?)<\/h4>/i)
  const p = block.match(/<p[^>]*>([\s\S]*?)<\/p>/i)
  return {
    label: h4 ? stripTags(h4[1]) : null,
    caption: p ? stripTags(p[1]) : null,
  }
}

function parsePage(file, html) {
  const entries = []
  const seen = new Set()

  const shotRe = /<div class="shot"[^>]*>([\s\S]*?)(?=<div class="shot"|<\/div>\s*<\/div>\s*<\/section|<\/div>\s*<\/div>\s*<div class="section)/gi
  for (const m of html.matchAll(shotRe)) {
    const block = m[1]
    const { label, caption } = blockText(block)
    const imgs = block.match(/<img\b[^>]*>/gi) || []
    const vids = block.match(/<video\b[^>]*>/gi) || []
    for (const tag of [...imgs, ...vids]) {
      const src = attr(tag, 'src') || attr(tag, 'data-full') || attr(tag, 'poster')
      const asset = assetName(src)
      if (!asset || seen.has(asset)) continue
      seen.add(asset)
      entries.push({
        page: file,
        asset,
        label: label || attr(tag, 'alt'),
        alt: attr(tag, 'alt'),
        caption,
        kind: tag.startsWith('<video') ? 'video' : 'image',
      })
    }
  }

  const tagRe = /<(img|video|source)\b[^>]*>/gi
  for (const m of html.matchAll(tagRe)) {
    const tag = m[0]
    const src = attr(tag, 'src') || attr(tag, 'data-full') || attr(tag, 'poster')
    if (!src || !/assets\//.test(src)) continue
    const asset = assetName(src)
    if (!asset || seen.has(asset)) continue
    seen.add(asset)
    entries.push({
      page: file,
      asset,
      label: attr(tag, 'alt'),
      alt: attr(tag, 'alt'),
      caption: null,
      kind: tag.startsWith('<video') || tag.startsWith('<source') ? 'video' : 'image',
    })
  }

  return entries
}

const pages = readdirSync(siteDir).filter((f) => f.endsWith('.html'))
const all = []
for (const file of pages) {
  const html = readFileSync(join(siteDir, file), 'utf8')
  all.push(...parsePage(file, html))
}

const byAsset = new Map()
for (const e of all) {
  const prev = byAsset.get(e.asset)
  if (!prev || (!prev.caption && e.caption)) byAsset.set(e.asset, e)
}
const manifest = [...byAsset.values()].sort((a, b) => a.asset.localeCompare(b.asset))

const out = join(here, 'script.json')
writeFileSync(out, JSON.stringify(manifest, null, 2) + '\n')
console.log(`Extracted ${manifest.length} unique illustrations from ${pages.length} pages → ${out}`)
const images = manifest.filter((e) => e.kind === 'image').length
console.log(`  ${images} images, ${manifest.length - images} videos/animations`)
