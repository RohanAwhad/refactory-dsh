import { defineConfig } from 'tsdown'

/**
 * Build the tool and the console process boundary as independent bundles.
 *
 * `lib/console.js` is its own entry because a Host service that only needs the
 * process boundary must not load the tool plugin to get it; the package declares
 * both as exports, so each is addressed on its own.
 */
export default defineConfig([
  {
    entry: ['lib/types/index.js'],
    outDir: 'lib',
    format: ['esm'],
    platform: 'node',
    target: 'es2024',
    fixedExtension: false,
    dts: false,
    clean: false,
  },
  {
    entry: ['lib/types/console.js'],
    outDir: 'lib',
    format: ['esm'],
    platform: 'node',
    target: 'es2024',
    fixedExtension: false,
    dts: false,
    clean: false,
  },
])
