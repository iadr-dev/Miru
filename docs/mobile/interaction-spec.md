88	
89	> **Animation library (mobile):** native = **`reanimated`** + `react-native-gesture-handler`
90	> (worklet-driven, runs on the UI thread); responsive-web fallback = **`motion/react`** (the web
91	> default, [../../web/CLAUDE.md](../../web/CLAUDE.md) §1). **GSAP does NOT apply to the native app** —
92	> it's DOM/SVG-only, so it can appear only on the responsive-web build, never in RN. The `spring.default`
93	> / duration tokens above are the shared contract; each platform maps them to its own engine.
94	
95	✓ **Shared token-export** (CSS-vars → generated RN theme object, single source, no drift) is specified
96	as a build rule in [../../web/CLAUDE.md](../../web/CLAUDE.md) §2.
97	