// jsdom does not implement ResizeObserver, but the Timeline component
// (ShopWindow.tsx) constructs one on mount. Without this polyfill any test that
// renders a SessionRow with more than one history entry throws
// "ResizeObserver is not defined", which is why the Timeline had no coverage.
// A no-op stub is enough: tests drive layout via the initial measure() call,
// not via observed resize events.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

if (!('ResizeObserver' in globalThis)) {
  ;(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub
}
