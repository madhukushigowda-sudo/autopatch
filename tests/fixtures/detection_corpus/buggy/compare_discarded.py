def reset(state):
    state.count == 0  # <<BUG>>
    return state
