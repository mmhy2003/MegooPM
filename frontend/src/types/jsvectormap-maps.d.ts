/**
 * jsvectormap ships its map data as plain JS files with no type declarations.
 *
 * Importing one registers that map on the constructor as a side effect — there
 * is no value to type, which is why the declaration is bare.
 */
declare module "jsvectormap/dist/maps/world";
declare module "jsvectormap/dist/maps/world-merc";
