/** @type {import('tailwindcss').Config} */
module.exports = {
	content: [
		"./examples/templates/**/*.html",
		"./fastapi_admin/templates/**/*.html",
	],
	theme: {
		extend: {
			colors: {
				darkk: "#2C3E50",
				text_darkk: "#1A1A1A",
				secondary: "#E67E22",
				bluee: "#EFF8FF",
			},
		},
	},
	plugins: [],
	// Important: This ensures Tailwind classes take precedence over Tabler UI
	important: true,
	// Safelist common classes we might use
	safelist: [
		// Layout & Flexbox
		"flex",
		"flex-1",
		"flex-col",
		"items-center",
		"justify-between",
		"space-x-2",
		"space-x-3",
		"space-x-4",
		"space-y-1",

		// Sizing & Spacing
		"h-screen",
		"h-8",
		"w-8",
		"w-64",
		"p-4",
		"p-6",
		"px-3",
		"px-4",
		"px-6",
		"py-2",
		"py-3",
		"pl-10",
		"pr-4",
		"mt-4",
		"top-2.5",
		"left-3",

		// Borders
		"border",
		"border-r",
		"border-b",
		"border-gray-200",
		"border-gray-300",
		"rounded-lg",

		// Typography
		"text-lg",
		"text-xl",
		"font-semibold",
		"text-gray-600",
		"text-gray-900",
		"text-gray-400",

		// Background
		"bg-white",
		"bg-gray-100",

		// Effects & States
		"hover:bg-gray-100",
		"hover:text-secondary",
		"focus:outline-none",
		"focus:border-blue-500",
		"bg-gray-100",

		// Display
		"relative",
		"absolute",
		"overflow-hidden",
		"overflow-y-auto",

		// Other
		"h-full",

		// Icon specific
		"w-6",
		"h-6",
		"flex",
		"items-center",
		"justify-center",

		// Badge specific
		"ml-auto",
		"bg-gray-200",
		"text-gray-600",
		"text-xs",
		"font-semibold",
		"px-2",
		"py-0.5",
		"rounded-full",

		// Original classes (keeping these for compatibility)
		"flex",
		"items-center",
		"justify-between",
		"p-4",
		"bg-blue-400",
		"text-2xl",
		"font-bold",
		"text-blue-800",
		"px-4",
		"py-2",
		"bg-blue-500",
		"text-white",
		"rounded",
		"hover:bg-blue-600",
	],
};
