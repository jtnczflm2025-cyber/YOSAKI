export type ReviewChange = {
  characterId: number
  entryId: string
  version: string
  narrativeId: string | null
  fields: { label: string; before: string; after: string }[]
}
