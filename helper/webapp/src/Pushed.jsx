import Vpn from './screens/Vpn.jsx'
import Matrix from './screens/Matrix.jsx'
import Code from './screens/Code.jsx'
import Downloads from './screens/Downloads.jsx'
import Help from './screens/Help.jsx'
import Books from './screens/Books.jsx'
import Upscale from './screens/Upscale.jsx'
import MemberDetail from './admin/MemberDetail.jsx'

// A screen pushed on top of the current root (shared by the real app and the owner's preview).
export default function Pushed({ entry }) {
  switch (entry.s) {
    case 'vpn': return <Vpn />
    case 'matrix': return <Matrix />
    case 'code': return <Code />
    case 'downloads': return <Downloads />
    case 'help': return <Help />
    case 'books': return <Books />
    case 'upscale': return <Upscale />
    case 'member': return <MemberDetail uid={entry.p.uid} />
    default: return null
  }
}
