// Vite config. Paths are relative (base './') so the built site works at any address, including a sub-folder.
//
// The designer's renders (reference/renders, 29 MB) are what "Compare with render" overlays. The dev server serves
// them from the project; a production build (npm run build -> dist/) copies them only when INCLUDE_RENDERS=1,
// because a hosted site is usually public and the renders are the designer's work. Without them the button is hidden.
import { cpSync } from 'node:fs';
import { defineConfig } from 'vite';

const includeRenders = process.env.INCLUDE_RENDERS === '1';

export default defineConfig(({ command }) => ({
  base: './',
  define: { __HAS_RENDERS__: JSON.stringify(command === 'serve' || includeRenders) },
  plugins: [
    {
      name: 'copy-reference-renders',
      apply: 'build',
      closeBundle() {
        if (includeRenders) cpSync('reference/renders', 'dist/reference/renders', { recursive: true });
      },
    },
  ],
}));
