/** jsdom has no WebGL, and sigma's edge and node programs read these at import time.
 *
 * A stub rather than a real context: nothing here renders, but a module that cannot even be
 * imported cannot be held to anything either, which is how an unregistered edge program shipped.
 */
const stubs = ["WebGLRenderingContext", "WebGL2RenderingContext"] as const;
for (const name of stubs) {
  if (!(name in globalThis)) {
    Object.defineProperty(globalThis, name, { value: class {}, writable: true });
  }
}

// jsdom ships no media query engine either, and sigma reads one for its pixel ratio
if (typeof window !== "undefined" && !window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    value: (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
    writable: true,
  });
}
