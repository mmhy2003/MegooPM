"use client"

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs"

import { cn } from "@/lib/utils"

function Tabs({ className, ...props }: TabsPrimitive.Root.Props) {
  return (
    <TabsPrimitive.Root
      data-slot="tabs"
      className={cn("flex flex-col gap-4", className)}
      {...props}
    />
  )
}

function TabsList({ className, children, ...props }: TabsPrimitive.List.Props) {
  return (
    <TabsPrimitive.List
      data-slot="tabs-list"
      className={cn(
        // `max-w-full` + `overflow-x-auto` let a list that is wider than its
        // container scroll instead of putting its last tabs out of reach — the
        // Security page has four, which is well past a phone's width.
        //
        // `justify-start`, not `justify-center`: a centred flex container whose
        // content overflows spills equally out of BOTH ends, and the overflow
        // at the start cannot be scrolled back to.
        "relative inline-flex h-9 w-fit max-w-full items-center justify-start overflow-x-auto rounded-lg bg-muted p-1 text-muted-foreground",
        className,
      )}
      {...props}
    >
      {children}
      <TabsPrimitive.Indicator
        data-slot="tabs-indicator"
        className="absolute top-1 left-0 z-0 h-[calc(100%-0.5rem)] w-(--active-tab-width) translate-x-(--active-tab-left) rounded-md bg-background shadow-sm transition-[translate,width] duration-200 ease-in-out"
      />
    </TabsPrimitive.List>
  )
}

function TabsTab({ className, ...props }: TabsPrimitive.Tab.Props) {
  return (
    <TabsPrimitive.Tab
      data-slot="tabs-tab"
      className={cn(
        // `data-active`, not `data-selected`: base-ui renamed the attribute on
        // <Tabs.Tab>, and the old name silently matched nothing, so the active
        // tab kept the muted foreground colour.
        //
        // `shrink-0` so a tab keeps its label width and the list scrolls,
        // rather than every tab being squeezed until the text is clipped.
        "relative z-10 inline-flex h-7 shrink-0 flex-1 items-center justify-center gap-1.5 rounded-md px-3 text-sm font-medium whitespace-nowrap transition-colors outline-none select-none data-active:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
        className,
      )}
      {...props}
    />
  )
}

function TabsPanel({ className, ...props }: TabsPrimitive.Panel.Props) {
  return (
    <TabsPrimitive.Panel
      data-slot="tabs-panel"
      className={cn("flex-1 outline-none", className)}
      {...props}
    />
  )
}

export { Tabs, TabsList, TabsTab, TabsPanel }
