/** jsdom lacks dialog top-layer behavior; native acceptance covers the focus trap. */
export function mockDialog(): () => void {
  const names = ['showModal', 'close'] as const
  const descriptors = names.map(name => Object.getOwnPropertyDescriptor(HTMLDialogElement.prototype, name))
  for (const name of names) Object.defineProperty(HTMLDialogElement.prototype, name, {
    configurable: true, value: function (this: HTMLDialogElement) { this.open = name === 'showModal' },
  })
  return () => { names.forEach((name, index) => {
    const descriptor = descriptors[index]
    if (descriptor) Object.defineProperty(HTMLDialogElement.prototype, name, descriptor)
    else Reflect.deleteProperty(HTMLDialogElement.prototype, name)
  }) }
}
