/** Small acquisition guards shared by the product control and local tests. */
export function mayStartScribe(running:boolean, disabled:boolean, attempted:boolean,
                               fixture:boolean): boolean {
  return !running && !disabled && (fixture || !attempted)
}

export async function acquireScribeMicrophone(
  getMedia: (constraints:MediaStreamConstraints) => Promise<MediaStream>,
  current: () => boolean,
): Promise<MediaStream|null> {
  const stream = await getMedia({audio:{channelCount:1,
    echoCancellation:true, noiseSuppression:true}, video:false})
  if (!current()) {
    stream.getTracks().forEach(track => track.stop())
    return null
  }
  return stream
}
