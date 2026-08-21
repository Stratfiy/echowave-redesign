/**
 * Make the Mintlify component vocabulary available to every `.mdx` page
 * without an import line in any of them.
 *
 * The 129 pages in this tree were authored for Mintlify, which resolves
 * `<Note>`, `<Card>`, `<Steps>` and friends implicitly. Astro does not: MDX is
 * ESM, so a component has to be imported where it is used. Three ways out of
 * that, and this is the third:
 *
 * 1. Add an import block to all 129 files. Works, but every future page needs
 *    the incantation and every diff carries it.
 * 2. `astro-auto-import`. Does exactly this — but its peer range stops at
 *    Astro 5, and pinning the whole site a major version back for one plugin
 *    is a worse trade than thirty lines here.
 * 3. Inject the import into the MDX AST at parse time. Version-independent,
 *    invisible in content, and the failure mode is loud: an unknown component
 *    is still an unresolved reference at build.
 *
 * Only components a page actually uses are imported, so an unused shim never
 * reaches the bundle.
 */

const COMPONENTS = [
  "Accordion",
  "AccordionGroup",
  "Card",
  "CardGroup",
  "Check",
  "CodeGroup",
  "Columns",
  "Expandable",
  "Frame",
  "Info",
  "Note",
  "ParamField",
  "ResponseField",
  "Screenshot",
  "Step",
  "Steps",
  "Tab",
  "Tabs",
  "Tip",
  "Warning",
];

const IMPORT_SOURCE = "@components/mintlify";

/** Names used anywhere in the tree, including inside nested JSX children. */
function collectUsed(tree) {
  const used = new Set();

  const visit = (node) => {
    if (!node || typeof node !== "object") return;

    if (
      (node.type === "mdxJsxFlowElement" || node.type === "mdxJsxTextElement") &&
      typeof node.name === "string" &&
      COMPONENTS.includes(node.name)
    ) {
      used.add(node.name);
    }

    for (const child of node.children ?? []) visit(child);
  };

  visit(tree);
  return used;
}

export function injectDocComponents() {
  return (tree) => {
    const used = collectUsed(tree);
    if (used.size === 0) return;

    const names = [...used].sort();
    const statement = `import { ${names.join(", ")} } from "${IMPORT_SOURCE}";`;

    // Prepended rather than appended: an import must be in scope for the JSX
    // below it, and MDX hoists module-level ESM anyway — but leading it keeps
    // the emitted module readable when debugging a build failure.
    tree.children.unshift({
      type: "mdxjsEsm",
      value: statement,
      data: {
        estree: {
          type: "Program",
          sourceType: "module",
          body: [
            {
              type: "ImportDeclaration",
              specifiers: names.map((name) => ({
                type: "ImportSpecifier",
                imported: { type: "Identifier", name },
                local: { type: "Identifier", name },
              })),
              source: { type: "Literal", value: IMPORT_SOURCE, raw: `"${IMPORT_SOURCE}"` },
              attributes: [],
            },
          ],
        },
      },
    });
  };
}
