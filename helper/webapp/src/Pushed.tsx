import Vpn from './screens/Vpn'
import Matrix from './screens/Matrix'
import Code from './screens/Code'
import Downloads from './screens/Downloads'
import Help from './screens/Help'
import Books from './screens/Books'
import Upscale from './screens/Upscale'
import MemberDetail from './admin/MemberDetail'
import type { StackEntry } from './types'

// A screen pushed on top of the current root (shared by the real app and the owner's preview).
export default function Pushed({ entry }: { entry: StackEntry }) {
  switch (entry.s) {
    case 'vpn': return <Vpn />
    case 'matrix': return <Matrix />
    case 'code': return <Code />
    case 'downloads': return <Downloads />
    case 'help': return <Help />
    case 'books': return <Books />
    case 'upscale': return <Upscale />
    case 'member': return entry.p ? <MemberDetail uid={entry.p.uid} /> : null
    default: return null
  }
}
