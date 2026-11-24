import './style.css';
import { App } from './app';

// exposed for scripted screenshots and debugging (tools/snap.mjs)
(window as unknown as { __app: App }).__app = new App(document.getElementById('app')!);
