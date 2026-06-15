from app.crud import Mapper, ModelWrapper
from app.models.broadcast_read_user import ArgusBroadcastReadUser
from app.utils.logger import Log


@ModelWrapper(ArgusBroadcastReadUser, Log("BroadcastReadDao"))
class BroadcastReadDao(Mapper):
    pass
