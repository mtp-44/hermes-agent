/**
 * PWA entry: install the browser `window.hermesDesktop` shim (side-effect
 * import, guaranteed to execute first), then boot the unmodified desktop
 * renderer.
 */
import './install'
import '../main'
