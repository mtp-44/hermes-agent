#!/usr/bin/env node

import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { gzipSync } from 'node:zlib'
import { fileURLToPath } from 'node:url'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const DESKTOP_DIR = path.resolve(SCRIPT_DIR, '..')
const DIST_DIR = path.join(DESKTOP_DIR, 'dist-pwa')
const MANIFEST_PATH = path.join(DIST_DIR, '.vite', 'manifest.json')

const LIMITS = Object.freeze({
  initialJavaScript: 2_000_000,
  initialCss: 100_000,
  optionalChunk: 1_500_000
})

function formatBytes(bytes) {
  return `${(bytes / 1_000_000).toFixed(2)} MB`
}

function readManifest() {
  if (!fs.existsSync(MANIFEST_PATH)) {
    throw new Error(`Missing ${path.relative(DESKTOP_DIR, MANIFEST_PATH)}. Run npm run build:pwa first.`)
  }

  return JSON.parse(fs.readFileSync(MANIFEST_PATH, 'utf8'))
}

function gzipSize(relativePath) {
  const absolutePath = path.join(DIST_DIR, relativePath)

  if (!fs.existsSync(absolutePath)) {
    throw new Error(`Manifest references missing output: ${relativePath}`)
  }

  return gzipSync(fs.readFileSync(absolutePath)).length
}

function collectInitialKeys(manifest, entryKey) {
  const initialKeys = new Set()

  function visit(key) {
    if (initialKeys.has(key)) {
      return
    }

    const chunk = manifest[key]

    if (!chunk) {
      throw new Error(`Manifest import ${key} has no chunk record.`)
    }

    initialKeys.add(key)
    for (const importedKey of chunk.imports ?? []) {
      visit(importedKey)
    }
  }

  visit(entryKey)

  return initialKeys
}

function uniqueFiles(manifest, keys, field) {
  const files = new Set()

  for (const key of keys) {
    const chunk = manifest[key]
    const values = field === 'file' ? [chunk.file] : (chunk[field] ?? [])

    for (const file of values) {
      if (file) {
        files.add(file)
      }
    }
  }

  return files
}

function main() {
  const manifest = readManifest()
  const entries = Object.entries(manifest).filter(([, chunk]) => chunk.isEntry)

  if (entries.length !== 1) {
    throw new Error(`Expected exactly one PWA entry in the manifest, found ${entries.length}.`)
  }

  const [entryKey] = entries[0]
  const initialKeys = collectInitialKeys(manifest, entryKey)
  const initialJavaScriptFiles = uniqueFiles(manifest, initialKeys, 'file')
  const initialCssFiles = uniqueFiles(manifest, initialKeys, 'css')
  const initialJavaScript = [...initialJavaScriptFiles].reduce((total, file) => total + gzipSize(file), 0)
  const initialCss = [...initialCssFiles].reduce((total, file) => total + gzipSize(file), 0)

  const allJavaScriptFiles = new Set(
    Object.values(manifest)
      .map(chunk => chunk.file)
      .filter(file => file.endsWith('.js'))
  )
  const optionalChunks = [...allJavaScriptFiles]
    .filter(file => !initialJavaScriptFiles.has(file))
    .map(file => ({ file, gzip: gzipSize(file) }))
    .sort((left, right) => right.gzip - left.gzip)
  const oversizedOptionalChunks = optionalChunks.filter(chunk => chunk.gzip > LIMITS.optionalChunk)
  const largestOptionalChunk = optionalChunks[0]

  console.log(
    [
      `PWA initial JavaScript: ${formatBytes(initialJavaScript)} gzip / ${formatBytes(LIMITS.initialJavaScript)}`,
      `PWA initial CSS:        ${formatBytes(initialCss)} gzip / ${formatBytes(LIMITS.initialCss)}`,
      largestOptionalChunk
        ? `Largest optional JS:    ${formatBytes(largestOptionalChunk.gzip)} gzip / ${formatBytes(LIMITS.optionalChunk)} (${largestOptionalChunk.file})`
        : 'Largest optional JS:    none'
    ].join('\n')
  )

  const failures = []

  if (initialJavaScript > LIMITS.initialJavaScript) {
    failures.push(
      `initial JavaScript exceeds its budget by ${formatBytes(initialJavaScript - LIMITS.initialJavaScript)}`
    )
  }

  if (initialCss > LIMITS.initialCss) {
    failures.push(`initial CSS exceeds its budget by ${formatBytes(initialCss - LIMITS.initialCss)}`)
  }

  for (const chunk of oversizedOptionalChunks) {
    failures.push(
      `${chunk.file} exceeds the optional chunk budget by ${formatBytes(chunk.gzip - LIMITS.optionalChunk)}`
    )
  }

  if (failures.length > 0) {
    console.error(`\nPWA bundle budget failed:\n- ${failures.join('\n- ')}`)
    process.exitCode = 1
  } else {
    console.log(
      `PWA bundle budget passed (${initialJavaScriptFiles.size} initial JS chunks, ${optionalChunks.length} optional).`
    )
  }
}

try {
  main()
} catch (error) {
  console.error(`PWA bundle budget failed: ${error instanceof Error ? error.message : String(error)}`)
  process.exitCode = 1
}
